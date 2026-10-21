"""Mini player, tray icon and sleep timer."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget

from .models import fmt
from .theme import C, icon


# ---- mini player -----------------------------------------------------------------------------
class MiniPlayer(QWidget):
    """Small always-on-top player. Drag anywhere to move; double-click to go back to the big window."""
    expandRequested = Signal()

    def __init__(self, window) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.win = window
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 104)
        self.setWindowTitle("Zuqongsoft Music")
        self._drag: QPoint | None = None
        self.cover: QPixmap | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 12, 18)
        lay.setSpacing(10)
        lay.addSpacing(72)                                   # cover is painted
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = QLabel("Nothing playing", objectName="miniTitle")
        self.artist = QLabel("", objectName="muted")
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.artist)
        col.addStretch(1)
        lay.addLayout(col, 1)

        def btn(name, tip, size=36, role="on_surface"):
            b = QToolButton()
            b.setIcon(icon(name, C[role]))
            b.setIconSize(QSize(size * 2 // 3, size * 2 // 3))
            b.setFixedSize(size, size)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        self.b_prev = btn("skip_previous", "Previous")
        self.b_play = btn("play_arrow", "Play / Pause", 44, "on_primary")
        self.b_play.setObjectName("fab")
        self.b_next = btn("skip_next", "Next")
        self.b_expand = btn("fullscreen", "Back to the full window", 32, "on_surface_variant")
        for b in (self.b_prev, self.b_play, self.b_next, self.b_expand):
            lay.addWidget(b)
        self.b_prev.clicked.connect(self.win.engine.previous)
        self.b_play.clicked.connect(self.win.engine.toggle)
        self.b_next.clicked.connect(lambda: self.win.engine.next())
        self.b_expand.clicked.connect(self.expandRequested)
        self._tick = QTimer(self, interval=500)
        self._tick.timeout.connect(self.update)

    def set_track(self, t: dict | None, cover: QPixmap | None) -> None:
        self.cover = cover
        fm = QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(t["title"], Qt.TextElideMode.ElideRight, 170) if t else "Nothing playing")
        self.artist.setText(QFontMetrics(self.artist.font()).elidedText(t["artist"], Qt.TextElideMode.ElideRight, 170)
                            if t else "")
        self.update()

    def set_playing(self, playing: bool) -> None:
        self.b_play.setIcon(icon("pause" if playing else "play_arrow", C["on_primary"]))

    def refresh_theme(self) -> None:
        for b, name, role in ((self.b_prev, "skip_previous", "on_surface"), (self.b_next, "skip_next", "on_surface"),
                              (self.b_expand, "fullscreen", "on_surface_variant")):
            b.setIcon(icon(name, C[role]))
        self.set_playing(self.win.engine.is_playing)
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._tick.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        shadow = QPainterPath()
        shadow.addRoundedRect(r.adjusted(0, 2, 0, 2), 24, 24)
        p.fillPath(shadow, QColor(0, 0, 0, 60))
        bg = QPainterPath()
        bg.addRoundedRect(r, 24, 24)
        p.fillPath(bg, QColor(C["surface_container_high"]))
        cover = QRectF(r.left() + 12, r.top() + 10, 72, 72)
        clip = QPainterPath()
        clip.addRoundedRect(cover, 14, 14)
        p.save()
        p.setClipPath(clip)
        if self.cover is not None and not self.cover.isNull():
            p.drawPixmap(cover, self.cover, QRectF(self.cover.rect()))
        else:
            p.fillRect(cover, QColor(C["surface_container_highest"]))
            icon("music_note", C["outline"]).paint(p, cover.adjusted(22, 22, -22, -22).toRect())
        p.restore()
        t = self.win.engine.current_track
        dur = (t or {}).get("duration_ms", 0) or 0
        if dur:                                               # thin progress line along the bottom
            frac = max(0.0, min(1.0, self.win.engine.position / dur))
            line = QRectF(r.left() + 20, r.bottom() - 9, r.width() - 40, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["secondary_container"]))
            p.drawRoundedRect(line, 1.5, 1.5)
            p.setBrush(QColor(C["primary"]))
            p.drawRoundedRect(QRectF(line.left(), line.top(), line.width() * frac, 3), 1.5, 1.5)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            self._drag = None
            self.win.settings.setValue("mini/pos", self.pos())

    def mouseDoubleClickEvent(self, _e) -> None:
        self.expandRequested.emit()


# ---- sleep timer -------------------------------------------------------------------------------
class SleepTimer(QObject):
    """Fades the music out and pauses after N minutes, or at the end of the current song."""
    changed = Signal(str)                  # short status ("" when off)
    FADE_MS = 30_000

    def __init__(self, engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.deadline: float | None = None
        self.end_of_song: dict | None = None
        self._fading = False
        self._timer = QTimer(self, interval=500)
        self._timer.timeout.connect(self._tick)
        engine.trackChanged.connect(self._track_changed)

    @property
    def active(self) -> bool:
        return self.deadline is not None or self.end_of_song is not None

    def start_minutes(self, minutes: int) -> None:
        self.cancel(quiet=True)
        self.deadline = time.monotonic() + minutes * 60
        self._timer.start()
        self._tick()

    def start_end_of_song(self) -> None:
        self.cancel(quiet=True)
        self.end_of_song = self.engine.current_track
        self._timer.start()
        self._tick()

    def cancel(self, quiet: bool = False) -> None:
        self.deadline = self.end_of_song = None
        self._timer.stop()
        if self._fading:
            self._fading = False
            self.engine.fade_to(1.0, 800)
        if not quiet:
            self.changed.emit("")

    def remaining_text(self) -> str:
        if self.end_of_song is not None:
            return "after this song"
        if self.deadline is None:
            return ""
        left = max(0, int(self.deadline - time.monotonic()))
        return f"{left // 60}:{left % 60:02d}"

    def _tick(self) -> None:
        if self.deadline is not None:
            left_ms = (self.deadline - time.monotonic()) * 1000
        elif self.end_of_song is not None:
            t = self.end_of_song
            left_ms = (t.get("duration_ms", 0) or 0) - self.engine.position
            fade = 8000
        else:
            return
        fade = self.FADE_MS if self.deadline is not None else 8000
        if left_ms <= fade and not self._fading and self.engine.is_playing:
            self._fading = True
            self.engine.fade_to(0.0, max(500, int(left_ms)), then=self._sleep)
        if self.deadline is not None and left_ms <= 0 and not self._fading:
            self._sleep()
        self.changed.emit(self.remaining_text())

    def _track_changed(self, _t) -> None:
        if self.end_of_song is not None and _t is not self.end_of_song:
            self._sleep()                    # the song ended before the fade finished

    def _sleep(self) -> None:
        if not self.active:
            return
        if hasattr(self.engine, "_hard_pause"):
            self.engine._hard_pause()            # already faded out: pause right away
        else:
            self.engine.pause()
        self._fading = False
        self.deadline = self.end_of_song = None
        self._timer.stop()
        self.changed.emit("")
        QTimer.singleShot(400, lambda: setattr(self.engine, "_fade", 1.0))   # next play starts at full level


def sleep_menu(menu: QMenu, timer: SleepTimer) -> None:
    menu.clear()
    status = timer.remaining_text()
    if status:
        head = menu.addAction(f"Sleeping {status}" if status.startswith("after") else f"Sleeping in {status}")
        head.setEnabled(False)
        menu.addAction("Cancel sleep timer").triggered.connect(lambda: timer.cancel())
        menu.addSeparator()
    for minutes in (15, 30, 45, 60, 90):
        menu.addAction(f"In {minutes} minutes").triggered.connect(lambda _c=False, m=minutes: timer.start_minutes(m))
    menu.addAction("At the end of this song").triggered.connect(timer.start_end_of_song)


# ---- tray icon ------------------------------------------------------------------------------------
class Tray(QObject):
    def __init__(self, window, app_icon: QIcon) -> None:
        super().__init__(window)
        self.win = window
        self.tray = QSystemTrayIcon(app_icon, window)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._fill)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.setToolTip("Zuqongsoft Music")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def set_track(self, t: dict | None, sleep: str = "") -> None:
        text = f"{t['title']} — {t['artist']}" if t else "Zuqongsoft Music"
        if sleep:
            text += f"\nSleep timer: {sleep}"
        self.tray.setToolTip(text[:120])

    def _fill(self) -> None:
        m, w, e = self.menu, self.win, self.win.engine
        m.clear()
        t = e.current_track
        head = m.addAction(f"{t['title']} — {t['artist']}" if t else "Nothing playing")
        head.setEnabled(False)
        m.addSeparator()
        m.addAction(icon("pause" if e.is_playing else "play_arrow"), "Pause" if e.is_playing else "Play"
                    ).triggered.connect(e.toggle)
        m.addAction(icon("skip_next"), "Next").triggered.connect(lambda: e.next())
        m.addAction(icon("skip_previous"), "Previous").triggered.connect(e.previous)
        m.addSeparator()
        m.addAction(icon("library_music"), "Show Zuqongsoft Music").triggered.connect(w.show_main)
        m.addAction(icon("mini_player"), "Mini player").triggered.connect(w.show_mini)
        sleep = m.addMenu(icon("bedtime"), "Sleep timer")
        sleep_menu(sleep, w.sleep)
        m.addSeparator()
        m.addAction("Quit").triggered.connect(w.quit_app)

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.win.toggle_main()
