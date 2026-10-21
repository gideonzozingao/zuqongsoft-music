"""Lyrics editor: type or paste lyrics, sync them by tapping along, save as .lrc.

Tap-to-sync: play the song, put the cursor on the first line and press Stamp line
(F8 or Ctrl+Enter) as each line starts. The stamp goes on the cursor's line and the
cursor moves to the next one. Nudge fixes a stamp that came in a little late or early.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QTextEdit, QVBoxLayout)

from . import lyrics as L
from .models import fmt
from .theme import C

REACTION_MS = 0          # set to e.g. 150 to compensate for tapping late


class LyricsEditor(QDialog):
    def __init__(self, window, track: dict, initial: str = "", parent=None) -> None:
        super().__init__(parent or window)
        self.win, self.engine, self.track = window, window.engine, track
        self.saved_path = ""
        self.setWindowTitle(f"Lyrics: {track['title']}")
        self.resize(640, 680)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)
        title = QLabel(f"Lyrics for “{track['title']}”", objectName="dialogTitle")
        title.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(QLabel(f"{track['artist']}  ·  {track['album']}", objectName="muted"))

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.btn_import = QPushButton("Import file…")
        self.btn_import.setToolTip("Load an .lrc or .txt file into the editor")
        self.btn_clear_stamps = QPushButton("Remove timestamps")
        tools.addWidget(self.btn_import)
        tools.addWidget(self.btn_clear_stamps)
        tools.addStretch(1)
        lay.addLayout(tools)

        self.edit = QPlainTextEdit()
        self.edit.setObjectName("lrcEdit")
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(11)
        self.edit.setFont(mono)
        self.edit.setPlaceholderText("Paste or type the lyrics here, one line per line.\n\n"
                                     "Then play the song and press Stamp line (F8) as each line starts.")
        self.edit.setPlainText(initial)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.edit, 1)

        # sync bar
        sync = QHBoxLayout()
        sync.setSpacing(6)
        self.btn_back = QPushButton("−3 s")
        self.btn_back.setToolTip("Jump back 3 seconds")
        self.btn_play = QPushButton("Play")
        self.btn_play.setProperty("kind", "tonal")
        self.btn_stamp = QPushButton("Stamp line  (F8)")
        self.btn_stamp.setProperty("kind", "filled")
        self.btn_stamp.setToolTip("Put the current playback time on the cursor's line, then move to the next line")
        self.btn_minus = QPushButton("−0.1 s")
        self.btn_plus = QPushButton("+0.1 s")
        for b in (self.btn_minus, self.btn_plus):
            b.setToolTip("Nudge the timestamp on the cursor's line")
        self.pos_lbl = QLabel("0:00.00", objectName="eqValue")
        self.pos_lbl.setMinimumWidth(64)
        for w in (self.btn_back, self.btn_play, self.btn_stamp, self.btn_minus, self.btn_plus):
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            sync.addWidget(w)
        sync.addStretch(1)
        sync.addWidget(self.pos_lbl)
        lay.addLayout(sync)
        self.sync_note = QLabel("", objectName="muted")
        self.sync_note.setWordWrap(True)
        lay.addWidget(self.sync_note)

        bottom = QHBoxLayout()
        self.status = QLabel("", objectName="muted")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Save as .lrc")
        self.btn_save.setProperty("kind", "filled")
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_save)
        lay.addLayout(bottom)
        for b in (self.btn_import, self.btn_clear_stamps, self.btn_cancel, self.btn_save):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_import.clicked.connect(self._import)
        self.btn_clear_stamps.clicked.connect(self._remove_stamps)
        self.btn_back.clicked.connect(lambda: self.engine.seek(max(0, self.engine.precise_position - 3000)))
        self.btn_play.clicked.connect(self.engine.toggle)
        self.btn_stamp.clicked.connect(self.stamp_line)
        self.btn_minus.clicked.connect(lambda: self.nudge(-100))
        self.btn_plus.clicked.connect(lambda: self.nudge(100))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self.save)
        self.edit.textChanged.connect(self._update_status)
        self.edit.cursorPositionChanged.connect(self._highlight_cursor_line)
        for seq in ("F8", "Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self.stamp_line)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save)

        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()
        self._update_status()
        self._highlight_cursor_line()

    # ---- helpers ----------------------------------------------------------------
    def _is_current(self) -> bool:
        cur = self.engine.current_track
        return bool(cur and cur["path"] == self.track["path"])

    def _tick(self) -> None:
        live = self._is_current()
        for b in (self.btn_back, self.btn_play, self.btn_stamp):
            b.setEnabled(live)
        self.btn_play.setText("Pause" if live and self.engine.is_playing else "Play")
        ms = self.engine.precise_position if live else 0
        self.pos_lbl.setText(f"{fmt(ms)}.{ms % 1000 // 10:02d}")
        self.sync_note.setText("" if live else "To sync, play this song first; the sync buttons work on "
                                               "the song that's playing.")
        self.sync_note.setVisible(not live)

    def _update_status(self) -> None:
        lines = [ln for ln in self.edit.toPlainText().split("\n") if L.strip_stamps(ln).strip()]
        timed = sum(1 for ln in lines if L.first_stamp(ln) is not None)
        if not lines:
            self.status.setText("No lyrics yet")
        elif timed == 0:
            self.status.setText(f"{len(lines)} lines, not synced (saves as plain lyrics)")
        else:
            self.status.setText(f"{timed} of {len(lines)} lines synced")
        self.btn_save.setEnabled(bool(lines))

    def _highlight_cursor_line(self) -> None:
        sel = QTextEdit.ExtraSelection()
        c = QColor(C["primary"])
        c.setAlphaF(0.16)
        sel.format.setBackground(c)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.edit.textCursor()
        sel.cursor.clearSelection()
        self.edit.setExtraSelections([sel])

    def _replace_line(self, cursor: QTextCursor, text: str) -> None:
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)

    # ---- actions --------------------------------------------------------------
    def stamp_line(self) -> None:
        if not self._is_current():
            return
        ms = max(0, self.engine.precise_position - REACTION_MS)
        cur = self.edit.textCursor()
        block = cur.block()
        if not block.isValid():
            return
        cur.beginEditBlock()
        self._replace_line(cur, L.fmt_stamp(ms) + L.strip_stamps(block.text()))
        cur.endEditBlock()
        nxt = block.next()
        if nxt.isValid():
            cur.setPosition(nxt.position())
        else:                                  # last line: add an empty one for the outro
            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            cur.insertText("\n")
        self.edit.setTextCursor(cur)
        self.edit.ensureCursorVisible()

    def nudge(self, delta: int) -> None:
        cur = self.edit.textCursor()
        text = cur.block().text()
        ms = L.first_stamp(text)
        if ms is None:
            return
        pos = cur.positionInBlock()
        self._replace_line(cur, L.fmt_stamp(ms + delta) + L.strip_stamps(text))
        cur.setPosition(cur.block().position() + min(pos, cur.block().length() - 1))
        self.edit.setTextCursor(cur)

    def _remove_stamps(self) -> None:
        lines = self.edit.toPlainText().split("\n")
        self.edit.setPlainText("\n".join(L.strip_stamps(ln) for ln in lines))

    def _import(self) -> None:
        start = os.path.dirname(self.track["path"])
        path, _ = QFileDialog.getOpenFileName(self, "Import lyrics", start,
                                              "Lyrics (*.lrc *.txt);;All files (*)")
        if path:
            text = L.read_lyrics_file(path)
            self.edit.setPlainText("\n".join(ln for ln in text.replace("\r\n", "\n").split("\n")
                                             if not L._TAG.match(ln.strip())).strip("\n"))

    def save(self) -> None:
        if not self.btn_save.isEnabled():
            return
        existing = L.existing_lrc(self.track["path"])
        if existing and self.property("confirm_overwrite"):
            if QMessageBox.question(self, "Replace lyrics",
                                    f"Replace the existing lyrics file?\n\n{existing}") != QMessageBox.StandardButton.Yes:
                return
        try:
            self.saved_path, fallback = L.save_lrc(self.track, self.edit.toPlainText())
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't save lyrics", str(exc))
            return
        if fallback:
            QMessageBox.information(self, "Lyrics saved",
                                    "The music folder is read-only, so the lyrics were saved in the "
                                    f"app's own folder instead:\n\n{self.saved_path}")
        self.accept()

    def done(self, r: int) -> None:
        self._timer.stop()
        super().done(r)
