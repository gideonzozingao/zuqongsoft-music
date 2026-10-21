"""Now-playing side panels: synced lyrics, the Up Next queue, and a star-rating bar."""
from __future__ import annotations

import bisect
import time

from PySide6.QtCore import (QAbstractListModel, QEasingCurve, QMimeData, QModelIndex, QRect, QRectF,
                            QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListView, QMenu,
                               QMessageBox, QPushButton,
                               QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
import os

from . import lyrics as L
from .lyrics import LyricsLoader
from .models import fmt, paint_stars, star_at
from .theme import C, icon


# ---- star rating bar -------------------------------------------------------------------
class StarBar(QWidget):
    rated = Signal(int)
    SIZE, GAP = 20, 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rating = 0
        self._hover = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(5 * self.SIZE + 4 * self.GAP + 2, self.SIZE + 6)
        self.setToolTip("Rate this song. Click the same star again to clear.")

    def set_rating(self, r: int) -> None:
        self.rating = r
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        paint_stars(p, QRectF(self.rect()), self._hover or self.rating, self.SIZE, self.GAP)

    def mouseMoveEvent(self, e) -> None:
        self._hover = star_at(e.position().x(), 0, self.SIZE, self.GAP)
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = 0
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.isEnabled():
            s = star_at(e.position().x(), 0, self.SIZE, self.GAP)
            self.rated.emit(0 if s == self.rating else s)


# ---- lyrics --------------------------------------------------------------------------------
class LyricsView(QWidget):
    """Karaoke-style view: the current line is highlighted and kept about a third of the way
    down; scrolling with the wheel pauses auto-follow for a few seconds; click a line to seek."""
    seekRequested = Signal(int)
    ANCHOR = 0.36
    GAP = 14
    PAD = 14
    FOLLOW_PAUSE = 4.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self._lines: list[tuple[int | None, str]] = []
        self._times: list[int] = []
        self._synced = False
        self._status = ""
        self._boxes: list[tuple[float, float]] = []      # (y, height) per line
        self._total = 0.0
        self._current = -1
        self._hover = -1
        self._scroll = 0.0
        self._pos, self._pos_at, self._playing = 0, time.monotonic(), False
        self._manual_until = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_scroll)
        self._timer = QTimer(self, interval=50)
        self._timer.timeout.connect(self._tick)
        # style overrides (the full-screen view uses big white text on its own background)
        self.font_px = 17
        self.color_current: QColor | None = None
        self.color_other: QColor | None = None
        self.fade_color: QColor | None = None
        self.align_left_plain = True

    def set_style(self, font_px: int, current: QColor | None = None, other: QColor | None = None,
                  fade: QColor | None = None) -> None:
        self.font_px, self.color_current, self.color_other, self.fade_color = font_px, current, other, fade
        self.GAP = max(14, font_px // 2)
        self._relayout()
        self.update()

    def lines(self) -> tuple[list, bool]:
        return self._lines, self._synced

    # -- content --
    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(self.font_px)
        f.setWeight(QFont.Weight.Bold)
        return f

    def set_lyrics(self, lines: list[tuple[int | None, str]], synced: bool) -> None:
        self._lines, self._synced, self._status = lines, synced, ""
        self._times = [ms if ms is not None else 0 for ms, _t in lines] if synced else []
        self._current = -1
        self._manual_until = 0.0
        self._relayout()
        self._anim.stop()
        self._scroll = self._target()
        self._tick()
        self.update()

    def set_status(self, text: str) -> None:
        self._lines, self._times, self._synced, self._status = [], [], False, text
        self._boxes, self._current = [], -1
        self._timer.stop()
        self.update()

    def set_position(self, ms: int, playing: bool) -> None:
        self._pos, self._pos_at, self._playing = ms, time.monotonic(), playing
        if self._synced and playing:
            self._timer.start()
        else:
            self._timer.stop()
        self._tick()

    def _now_ms(self) -> int:
        if not self._playing:
            return self._pos
        return self._pos + int((time.monotonic() - self._pos_at) * 1000)

    # -- layout / scrolling --
    def _relayout(self) -> None:
        fm = QFontMetrics(self._font())
        width = max(50, self.width() - 2 * self.PAD)
        y, boxes = 0.0, []
        for _ms, text in self._lines:
            h = fm.boundingRect(QRect(0, 0, width, 100000), Qt.TextFlag.TextWordWrap, text or "♪").height()
            boxes.append((y, float(h)))
            y += h + self.GAP
        self._boxes, self._total = boxes, y

    def _target(self) -> float:
        if not self._boxes:
            return 0.0
        if not self._synced:
            return max(-self.PAD, min(self._scroll, self._total - self.height() * 0.6))
        y, h = self._boxes[max(0, self._current)]
        return y + h / 2 - self.height() * self.ANCHOR

    def _set_scroll(self, v) -> None:
        self._scroll = float(v)
        self.update()

    def _follow(self) -> None:
        if time.monotonic() < self._manual_until:
            return
        self._anim.stop()
        self._anim.setStartValue(self._scroll)
        self._anim.setEndValue(self._target())
        self._anim.start()

    def _tick(self) -> None:
        if not self._synced or not self._times:
            return
        idx = bisect.bisect_right(self._times, self._now_ms()) - 1
        if idx != self._current:
            self._current = idx
            self._follow()
            self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()
        self._anim.stop()
        self._scroll = self._target() if self._synced else max(-self.PAD, self._scroll)

    def wheelEvent(self, e) -> None:
        if not self._boxes:
            return
        self._anim.stop()
        lo = -self.height() * self.ANCHOR if self._synced else -self.PAD
        hi = max(lo, self._total - self.height() * (0.5 if self._synced else 0.8))
        self._scroll = max(lo, min(hi, self._scroll - e.angleDelta().y() / 2))
        self._manual_until = time.monotonic() + self.FOLLOW_PAUSE
        self.update()

    def _line_at(self, y: float) -> int:
        for i, (by, bh) in enumerate(self._boxes):
            top = by - self._scroll
            if top - self.GAP / 2 <= y <= top + bh + self.GAP / 2:
                return i
        return -1

    def mouseMoveEvent(self, e) -> None:
        i = self._line_at(e.position().y()) if self._synced else -1
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        i = self._line_at(e.position().y()) if self._synced else -1
        if i >= 0 and self._lines[i][0] is not None:
            self._manual_until = 0.0
            self.seekRequested.emit(self._lines[i][0])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._status:
            f = QFont(self.font())
            f.setPixelSize(max(14, self.font_px - 3))
            p.setFont(f)
            p.setPen(QColor(self.color_other or C["on_surface_variant"]))
            p.drawText(self.rect().adjusted(self.PAD, 0, -self.PAD, 0),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._status)
            return
        bold, regular = self._font(), self._font()
        regular.setWeight(QFont.Weight.DemiBold)
        width = self.width() - 2 * self.PAD
        for i, ((_ms, text), (y, h)) in enumerate(zip(self._lines, self._boxes)):
            top = y - self._scroll
            if top + h < -20 or top > self.height() + 20:
                continue
            rect = QRectF(self.PAD, top, width, h)
            if i == self._hover:
                bg = QPainterPath()
                bg.addRoundedRect(rect.adjusted(-8, -5, 8, 5), 10, 10)
                hover = QColor(C["on_surface"])
                hover.setAlphaF(0.08)
                p.fillPath(bg, hover)
            if not self._synced:
                colour, font = QColor(self.color_other or C["on_surface"]), regular
                colour.setAlphaF(0.85)
            elif i == self._current:
                colour, font = QColor(self.color_current or C["primary"]), bold
            else:
                colour, font = QColor(self.color_other or C["on_surface_variant"]), regular
                colour.setAlphaF(0.45 if i < self._current else 0.75)
            p.setFont(font)
            p.setPen(colour)
            align = (Qt.AlignmentFlag.AlignHCenter if self._synced else Qt.AlignmentFlag.AlignLeft)
            p.drawText(rect, align | Qt.TextFlag.TextWordWrap, text or "♪")
        # fade top and bottom into the panel colour
        if self.fade_color is not None and self.fade_color.alpha() == 0:
            return
        bg = QColor(self.fade_color or C["surface_container_low"])
        for y0, y1 in ((0, 28), (self.height(), self.height() - 28)):
            g = QLinearGradient(0, y0, 0, y1)
            g.setColorAt(0, bg)
            clear = QColor(bg)
            clear.setAlpha(0)
            g.setColorAt(1, clear)
            p.fillRect(QRectF(0, min(y0, y1), self.width(), 28), g)


class LyricsPanel(QWidget):
    seekRequested = Signal(int)
    lyricsChanged = Signal()          # the view's lines/status changed (full-screen view mirrors it)

    def __init__(self, library, settings, window, parent=None) -> None:
        super().__init__(parent)
        self.win = window
        self._lyr = None
        self.settings = settings
        self.loader = LyricsLoader(library, self)
        self.library = library
        self._track: dict | None = None
        self._active = False
        self._pending_online = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        self.source = QLabel("", objectName="muted")
        self.more = QToolButton(objectName="iconMenu")
        theme.bind(self.more, "more_vert")
        self.more.setFixedSize(32, 32)
        self.more.setIconSize(QSize(20, 20))
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setToolTip("Lyrics options")
        self.menu = QMenu(self)
        self.menu.aboutToShow.connect(self._fill_menu)
        self.more.setMenu(self.menu)
        head.addWidget(self.source, 1)
        head.addWidget(self.more)
        self.view = LyricsView()
        # shown when a song has no lyrics
        self.empty_row = QWidget()
        er = QHBoxLayout(self.empty_row)
        er.setContentsMargins(0, 0, 0, 8)
        er.addStretch(1)
        self.btn_add = QPushButton("Add lyrics")
        self.btn_add.setProperty("kind", "tonal")
        self.btn_import = QPushButton("Import file…")
        for b in (self.btn_add, self.btn_import):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            er.addWidget(b)
        er.addStretch(1)
        self.empty_row.hide()
        self.btn_add.clicked.connect(self.edit_lyrics)
        self.btn_import.clicked.connect(self.import_file)
        lay.addLayout(head)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.empty_row)
        self.view.seekRequested.connect(self.seekRequested)
        self.loader.loaded.connect(self._loaded)
        self.view.set_status("Lyrics appear here when a song plays.")

    @property
    def online(self) -> bool:
        return self.settings.value("lyrics/online", True, type=bool)

    def _fill_menu(self) -> None:
        self.menu.clear()
        has_track, lyr = self._track is not None, self._lyr
        has = bool(lyr and not lyr.empty)
        for name, text, fn, ok in (
                ("edit", "Edit lyrics…" if has else "Add lyrics…", self.edit_lyrics, has_track),
                ("file_upload", "Import .lrc or .txt file…", self.import_file, has_track),
                ("music_note", "Save as .lrc file", self.save_current, has and lyr.source != "lrc file"),
                ("folder", "Show .lrc file", lambda: self.win._reveal(lyr.file), bool(lyr and lyr.file)),
                ("delete", "Delete .lrc file…", self.delete_lrc, bool(lyr and lyr.file))):
            a = self.menu.addAction(icon(name), text)
            a.setEnabled(ok)
            a.triggered.connect(lambda _c=False, f=fn: f())
        self.menu.addSeparator()
        act = self.menu.addAction("Search online (LRCLIB)")
        act.setCheckable(True)
        act.setChecked(self.online)
        act.toggled.connect(self._set_online)
        again = self.menu.addAction("Search again")
        again.setEnabled(self._track is not None and self.online)
        again.triggered.connect(self._search_again)

    # ---- adding / editing lyrics ----------------------------------------------
    def edit_lyrics(self) -> None:
        if not self._track:
            return
        from .lyrics_editor import LyricsEditor
        lyr = self._lyr
        text = L.to_lrc(lyr.lines) if lyr and not lyr.empty else ""
        dlg = LyricsEditor(self.win, self._track, text)
        dlg.setProperty("confirm_overwrite", not (lyr and lyr.file))
        if dlg.exec() and dlg.saved_path:
            self._saved(dlg.saved_path)

    def import_file(self) -> None:
        if not self._track:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import lyrics", os.path.dirname(self._track["path"]),
                                              "Lyrics (*.lrc *.txt);;All files (*)")
        if not path:
            return
        text = L.read_lyrics_file(path)
        lines, synced = L.parse_lrc(text)
        if not lines:
            QMessageBox.warning(self, "Import lyrics", "That file doesn't contain any lyrics.")
            return
        existing = L.existing_lrc(self._track["path"])
        if existing and os.path.normcase(os.path.abspath(existing)) != os.path.normcase(os.path.abspath(path)):
            if QMessageBox.question(self, "Replace lyrics", "This song already has a lyrics file. Replace it?"
                                    ) != QMessageBox.StandardButton.Yes:
                return
        try:
            saved, _fallback = L.save_lrc(self._track, L.to_lrc(lines))
        except OSError as exc:
            QMessageBox.warning(self, "Import lyrics", f"Couldn't save the lyrics: {exc}")
            return
        self._saved(saved, "synced" if synced else "plain")

    def save_current(self) -> None:
        if self._track and self._lyr and not self._lyr.empty:
            try:
                saved, _fallback = L.save_lrc(self._track, L.to_lrc(self._lyr.lines))
            except OSError as exc:
                QMessageBox.warning(self, "Save lyrics", f"Couldn't save the lyrics: {exc}")
                return
            self._saved(saved)

    def delete_lrc(self) -> None:
        lyr = self._lyr
        if not (lyr and lyr.file):
            return
        if QMessageBox.question(self, "Delete lyrics file", f"Delete this lyrics file?\n\n{lyr.file}"
                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(lyr.file)
        except OSError as exc:
            QMessageBox.warning(self, "Delete lyrics file", str(exc))
            return
        self.win.statusBar().showMessage("Lyrics file deleted", 4000)
        self.load(self._track)

    def _saved(self, path: str, kind: str = "") -> None:
        what = f" ({kind} lyrics)" if kind else ""
        self.win.statusBar().showMessage(f"Saved {os.path.basename(path)}{what}", 6000)
        self.load(self._track)

    def _set_online(self, on: bool) -> None:
        self.settings.setValue("lyrics/online", on)
        if self._track:
            self.load(self._track)

    def _search_again(self) -> None:
        if self._track:
            self.library.forget_lyrics(self._track["path"])
            self._pending_online = False
            self._request(online=True)

    def set_active(self, active: bool) -> None:
        """Online lookups only happen while the panel is shown."""
        self._active = active
        if active and self._pending_online and self._track:
            self._pending_online = False
            self._request(online=True)

    def load(self, track: dict | None) -> None:
        self._track = track
        self._lyr = None
        self._pending_online = False
        self.empty_row.hide()
        self.source.setText("")
        if track is None:
            self.view.set_status("Lyrics appear here when a song plays.")
            return
        self.view.set_status("Loading lyrics…")
        self.lyricsChanged.emit()
        self._request(online=self.online and self._active)

    def _request(self, online: bool) -> None:
        self.loader.request(self._track, online)

    def _loaded(self, path: str, lyr, status: str) -> None:
        if not self._track or path != self._track["path"]:
            return                                  # an old track's answer
        hint = ""
        self._lyr = lyr if status == "ok" else None
        QTimer.singleShot(0, self.lyricsChanged.emit)
        self.empty_row.setVisible(status in ("none", "offline") and not (self.online and not self._active))
        if status == "ok" and lyr is not None:
            if lyr.instrumental:
                self.view.set_status("♪  Instrumental")
                self.source.setText("LRCLIB")
                return
            self.view.set_lyrics(lyr.lines, lyr.synced)
            self.source.setText(("Synced lyrics" if lyr.synced else "Lyrics, not synced") + f"  ·  {lyr.source}")
        elif status == "searching":
            self.view.set_status("Searching LRCLIB…")
        elif status == "offline":
            if self.online and not self._active:
                self._pending_online = True
                self.view.set_status("Loading lyrics…")
            else:
                self.view.set_status("No lyrics in this song's file, and online search is off." + hint)
        elif status == "none":
            self.view.set_status("No lyrics found for this song." + hint)
        else:
            self.view.set_status("Couldn't reach LRCLIB to look up lyrics. Check your internet "
                                 "connection, then use ⋮ › Search again.")

    def shutdown(self) -> None:
        self.loader.shutdown()


# ---- Up Next ---------------------------------------------------------------------------------
class QueueModel(QAbstractListModel):
    MIME = "application/x-musicplayer-queue-row"
    moveRequested = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[dict, bool]] = []

    def set_items(self, items: list[tuple[dict, bool]]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t, user = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return t["title"]
        if role == Qt.ItemDataRole.UserRole:
            return t, user
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{t['title']}\n{t['artist']} · {t['album']}\nDrag to reorder"
        return None

    def flags(self, index):
        if index.isValid():
            return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        return Qt.ItemFlag.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def supportedDragActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [self.MIME]

    def mimeData(self, indexes) -> QMimeData:
        md = QMimeData()
        md.setData(self.MIME, str(indexes[0].row()).encode())
        return md

    @staticmethod
    def _dst(src: int, before: int) -> int:
        return before - 1 if before > src else before

    def moveRows(self, src_parent, src_row, count, dst_parent, dst_child) -> bool:
        # QListView's internal move lands here; the engine does the real move.
        dst = self._dst(src_row, dst_child)
        if dst != src_row:
            self.moveRequested.emit(src_row, dst)
        return True

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        if not data.hasFormat(self.MIME):
            return False
        src = int(bytes(data.data(self.MIME)).decode() or -1)
        before = row if row >= 0 else (parent.row() if parent.isValid() else self.rowCount())
        dst = self._dst(src, before)
        if 0 <= src and dst != src:
            self.moveRequested.emit(src, dst)
        return False                    # never let the view delete the dragged row itself


class QueueDelegate(QStyledItemDelegate):
    H = 56

    def __init__(self, covers, parent=None) -> None:
        super().__init__(parent)
        self.covers = covers

    def sizeHint(self, option, index) -> QSize:
        return QSize(200, self.H)

    def paint(self, p: QPainter, option, index) -> None:
        t, user = index.data(Qt.ItemDataRole.UserRole)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            bg = QPainterPath()
            bg.addRoundedRect(r, 12, 12)
            c = QColor(C["secondary_container"]) if selected else QColor(C["on_surface"])
            if not selected:
                c.setAlphaF(0.08)
            p.fillPath(bg, c)
        thumb = QRectF(r.left() + 8, r.center().y() - 20, 40, 40)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 8, 8)
        p.save()
        p.setClipPath(clip)
        pix = self.covers.get(t["path"])
        if pix is not None and not pix.isNull():
            p.drawPixmap(thumb, pix, QRectF(pix.rect()))
        else:
            p.fillRect(thumb, QColor(C["surface_container_highest"]))
            icon("music_note", C["outline"]).paint(p, thumb.adjusted(10, 10, -10, -10).toRect())
        p.restore()
        fg = C["on_secondary_container"] if selected else C["on_surface"]
        right = r.right() - 10
        tag_w = 0
        f3 = QFont(option.font)
        f3.setPixelSize(11)
        f3.setWeight(QFont.Weight.DemiBold)
        if user:
            tag_w = QFontMetrics(f3).horizontalAdvance("QUEUED") + 14
            tag = QRectF(right - tag_w, r.center().y() - 10, tag_w, 20)
            path = QPainterPath()
            path.addRoundedRect(tag, 6, 6)
            p.fillPath(path, QColor(C["primary_container"]))
            p.setFont(f3)
            p.setPen(QColor(C["on_primary_container"]))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, "QUEUED")
            tag_w += 8
        x = thumb.right() + 12
        w = int(right - tag_w - x)
        f1 = QFont(option.font)
        f1.setPixelSize(14)
        f1.setWeight(QFont.Weight.DemiBold)
        p.setFont(f1)
        p.setPen(QColor(fg))
        p.drawText(QRectF(x, r.center().y() - 19, w, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f1).elidedText(t["title"], Qt.TextElideMode.ElideRight, w))
        f2 = QFont(option.font)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(C["on_surface_variant"]))
        p.drawText(QRectF(x, r.center().y() + 1, w, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(f"{t['artist']}  ·  {fmt(t['duration_ms'])}",
                                               Qt.TextElideMode.ElideRight, w))
        p.restore()


class QueuePanel(QWidget):
    def __init__(self, engine, covers, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.count = QLabel("", objectName="muted")
        self.btn_clear = QPushButton("Clear queue")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip("Remove the songs you queued (the album or playlist stays)")
        self.btn_autoplay = QPushButton("Autoplay")
        self.btn_autoplay.setCheckable(True)
        self.btn_autoplay.setProperty("kind", "tonal")
        self.btn_autoplay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_autoplay.setToolTip("When Up Next runs low, keep adding songs that fit what's playing")
        head.addWidget(self.count, 1)
        head.addWidget(self.btn_clear)
        head.addWidget(self.btn_autoplay)
        self.model = QueueModel()
        v = self.view = QListView()
        v.setModel(self.model)
        v.setItemDelegate(QueueDelegate(covers, v))
        v.setObjectName("queue")
        v.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        v.setDragEnabled(True)
        v.setAcceptDrops(True)
        v.setDropIndicatorShown(True)
        v.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        v.setDefaultDropAction(Qt.DropAction.MoveAction)
        v.setDragDropOverwriteMode(False)
        v.setMouseTracking(True)
        v.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty = QLabel("Nothing up next.\n\nRight-click any song and choose Play next or\nAdd to queue, "
                            "or turn on Autoplay.",
                            objectName="muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(v)
        self.stack.addWidget(self.empty)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)

        covers.changed.connect(v.viewport().update)
        engine.queueChanged.connect(self.refresh)
        self.model.moveRequested.connect(self._move, Qt.ConnectionType.QueuedConnection)
        v.activated.connect(lambda idx: engine.jump_to_upcoming(idx.row()))
        v.customContextMenuRequested.connect(self._menu)
        self.btn_clear.clicked.connect(engine.clear_queued)
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), v)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._remove_selected)
        self.refresh()

    def refresh(self) -> None:
        items = self.engine.upcoming_items()
        self.model.set_items(items)
        queued = sum(1 for _t, u in items if u)
        total = sum(t["duration_ms"] for t, _u in items)
        if items:
            self.count.setText(f"{len(items)} song{'s' if len(items) != 1 else ''}  ·  {fmt(total)}")
        else:
            self.count.setText("")
        self.btn_clear.setVisible(queued > 0)
        self.stack.setCurrentWidget(self.view if items else self.empty)

    def _move(self, src: int, dst: int) -> None:
        self.engine.move_upcoming(src, dst)
        self.view.setCurrentIndex(self.model.index(dst, 0))

    def _remove_selected(self) -> None:
        rows = [i.row() for i in self.view.selectionModel().selectedIndexes()]
        if rows:
            self.engine.remove_upcoming(rows)

    def _menu(self, pos) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        m = QMenu(self)
        for name, text, fn in (("play_arrow", "Play now", lambda: self.engine.jump_to_upcoming(row)),
                               ("skip_next", "Play next", lambda: self.engine.move_upcoming(row, 0)),
                               ("remove", "Remove from Up Next", lambda: self.engine.remove_upcoming([row]))):
            a = m.addAction(icon(name), text)
            a.triggered.connect(lambda _c=False, f=fn: f())
        m.exec(self.view.viewport().mapToGlobal(pos))
