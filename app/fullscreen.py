"""Full-screen Now Playing and karaoke controls.

The background is a slowly drifting blend of the cover's own colours (rendered small and
scaled up, so it costs almost nothing). Cover and song details on the left, synced lyrics on
the right, a live spectrum along the bottom. Controls appear when you move the mouse and
hide again (with the cursor) after three seconds.

Keys: Esc / F11 exit, Space play/pause, ←/→ seek 5 s, N / P next / previous,
↑/↓ volume, K karaoke, L lyrics.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from .models import fmt
from .panels import LyricsView
from .theme import icon

WHITE = QColor(255, 255, 255)


def palette_from_image(img) -> list[QColor]:
    """Up to three distinct vivid colours of a cover (most prominent first)."""
    if img is None or img.isNull():
        return []
    small = img.convertToFormat(QImage.Format.Format_RGB32).scaled(
        40, 40, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    buckets = [[0.0, 0.0, 0.0, 0.0] for _ in range(24)]
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            h, s, v = c.hsvHueF(), c.hsvSaturationF(), c.valueF()
            if h < 0 or s < 0.2 or v < 0.15:
                continue
            w = (s * v) ** 2
            b = buckets[int(h * 24) % 24]
            b[0] += w
            b[1] += c.red() * w
            b[2] += c.green() * w
            b[3] += c.blue() * w
    order = sorted(range(24), key=lambda i: -buckets[i][0])
    picked: list[int] = []
    for i in order:
        if buckets[i][0] < 0.5:
            break
        if all(min(abs(i - j), 24 - abs(i - j)) >= 3 for j in picked):
            picked.append(i)
        if len(picked) == 3:
            break
    return [QColor(int(buckets[i][1] / buckets[i][0]), int(buckets[i][2] / buckets[i][0]),
                   int(buckets[i][3] / buckets[i][0])) for i in picked]


def _tone(colour: QColor, lightness: float, chroma: float) -> QColor:
    L, a, b = theme._lab(colour.name())
    return QColor(theme._tone(math.atan2(b, a), chroma, lightness))


# ---- karaoke controls -----------------------------------------------------------------------
class KaraokeControls(QWidget):
    """On/off, vocal reduction and key change. `overlay=True` styles it for the dark full-screen view."""

    def __init__(self, window, overlay: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.win = window
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.on = QPushButton("Karaoke")
        self.on.setCheckable(True)
        self.on.setProperty("kind", "tonal")
        self.on.setToolTip("Turn the lead vocal down and change the key (K)")
        self.vocal = QSlider(Qt.Orientation.Horizontal)
        self.vocal.setRange(0, 100)
        self.vocal.setFixedWidth(130)
        self.vocal.setToolTip("Vocal reduction")
        self.vocal_lbl = QLabel("")
        self.key_down = QPushButton("−")
        self.key_up = QPushButton("+")
        for b in (self.key_down, self.key_up):
            b.setFixedWidth(40)
        self.key_lbl = QLabel("")
        self.key_lbl.setMinimumWidth(64)
        self.key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self.on, QLabel("Vocals"), self.vocal, self.vocal_lbl, QLabel("Key"), self.key_down,
                  self.key_lbl, self.key_up):
            lay.addWidget(w)
        if overlay:
            self.setStyleSheet(
                "QLabel { color: rgba(255,255,255,0.85); background: transparent; }"
                "QPushButton { color: white; background: rgba(255,255,255,0.14); border: none; border-radius: 18px;"
                " min-height: 36px; padding: 0 14px; font-weight: 600; }"
                "QPushButton:hover { background: rgba(255,255,255,0.24); }"
                "QPushButton:checked { background: white; color: black; }"
                "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.25); border-radius: 2px; }"
                "QSlider::sub-page:horizontal { background: white; border-radius: 2px; }"
                "QSlider::handle:horizontal { background: white; width: 14px; height: 14px; margin: -5px 0;"
                " border-radius: 7px; }")
        self.on.toggled.connect(lambda on: self.win.set_karaoke(on=on))
        self.vocal.valueChanged.connect(lambda v: self.win.set_karaoke(vocal=v / 100))
        self.key_down.clicked.connect(lambda: self.win.set_karaoke(key=self.win.karaoke_state()[2] - 1))
        self.key_up.clicked.connect(lambda: self.win.set_karaoke(key=self.win.karaoke_state()[2] + 1))
        self.sync()

    def sync(self) -> None:
        on, vocal, key = self.win.karaoke_state()
        for w, v in ((self.on, on), (self.vocal, round(vocal * 100))):
            w.blockSignals(True)
            w.setChecked(v) if w is self.on else w.setValue(v)
            w.blockSignals(False)
        self.vocal_lbl.setText(f"{round(vocal * 100)}%")
        self.key_lbl.setText("Original" if key == 0 else f"{key:+d} semitone{'s' if abs(key) != 1 else ''}"
                             .replace(" semitones", " st").replace(" semitone", " st"))
        for w in (self.vocal, self.key_down, self.key_up):
            w.setEnabled(on)
        self.key_down.setEnabled(on and key > -6)
        self.key_up.setEnabled(on and key < 6)


class KaraokeDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.setWindowTitle("Karaoke")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Karaoke", objectName="dialogTitle"))
        hint = QLabel("Turns the lead vocal down by cancelling what's identical in both speakers (most "
                      "lead vocals are). Works well on many songs, less on others. Key change keeps the "
                      "tempo.", objectName="muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.controls = KaraokeControls(window)
        lay.addWidget(self.controls)
        full = QPushButton("Full-screen lyrics")
        full.setProperty("kind", "filled")
        full.clicked.connect(lambda: (self.close(), window.show_fullscreen()))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(full)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)


# ---- full-screen view ---------------------------------------------------------------------------------
class FullScreenView(QWidget):
    closed = Signal()

    def __init__(self, window) -> None:
        super().__init__(None)
        self.win = window
        self.setWindowTitle("Zuqongsoft Music")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.track: dict | None = None
        self.cover: QPixmap | None = None
        self.colors = [QColor("#6750A4"), QColor("#B0306A"), QColor("#0061A4")]
        self.base = QColor("#141218")
        self.blobs = [QColor("#4F378B")] * 3
        self.rta: list[float] | None = None
        self.show_lyrics = True
        self._phase = 0.0
        self._bg = QImage(320, 180, QImage.Format.Format_RGB32)
        self.lyrics = LyricsView(self)
        self.lyrics.seekRequested.connect(lambda ms: self.win.engine.seek(ms))
        self._paint_bg()

        self.controls = QWidget(self)
        self.controls.setStyleSheet(
            "QToolButton { background: rgba(255,255,255,0.12); border: none; border-radius: 28px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.24); }"
            "QToolButton#big { background: white; border-radius: 36px; }")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(16)

        def btn(name, tip, size=56, obj=""):
            b = QToolButton(objectName=obj)
            b.setIcon(icon(name, "#000000" if obj == "big" else "#FFFFFF"))
            b.setIconSize(QSize(size // 2, size // 2))
            b.setFixedSize(size, size)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        self.b_prev = btn("skip_previous", "Previous (P)")
        self.b_play = btn("pause", "Play / Pause (Space)", 72, "big")
        self.b_next = btn("skip_next", "Next (N)")
        self.b_lyrics = btn("lyrics" if "lyrics" in theme.PATHS else "queue_music", "Lyrics on/off (L)")
        self.b_exit = btn("close", "Exit full screen (Esc)")
        self.karaoke = KaraokeControls(window, overlay=True)
        for b in (self.b_prev, self.b_play, self.b_next):
            cl.addWidget(b)
        cl.addSpacing(24)
        cl.addWidget(self.karaoke)
        cl.addSpacing(12)
        cl.addWidget(self.b_lyrics)
        cl.addWidget(self.b_exit)
        self.b_prev.clicked.connect(self.win.engine.previous)
        self.b_play.clicked.connect(self.win.engine.toggle)
        self.b_next.clicked.connect(lambda: self.win.engine.next())
        self.b_lyrics.clicked.connect(self.toggle_lyrics)
        self.b_exit.clicked.connect(self.close)

        self._anim = QTimer(self, interval=40)          # 25 fps background drift
        self._anim.timeout.connect(self._tick)
        self._idle = QTimer(self, singleShot=True, interval=3000)
        self._idle.timeout.connect(self._hide_controls)
        self._rta_timer = QTimer(self, interval=80)
        self._rta_timer.timeout.connect(self._poll_rta)

    # ---- content -------------------------------------------------------------------
    def set_track(self, track: dict | None, cover: QPixmap | None) -> None:
        self.track, self.cover = track, cover
        colors = palette_from_image(cover.toImage()) if cover is not None and not cover.isNull() else []
        if not colors:
            colors = [QColor(theme.C["primary"])]
        while len(colors) < 3:                          # fill with hue-shifted relatives
            h, s, v, _a = colors[0].getHsvF()
            colors.append(QColor.fromHsvF(((h if h >= 0 else 0.7) + 0.08 * len(colors)) % 1, max(0.4, s), v))
        self.colors = colors
        self.base = _tone(colors[0], 8, 14)
        self.blobs = [_tone(c, 38 + 6 * i, 55) for i, c in enumerate(colors)]
        self.update()

    def sync_lyrics(self) -> None:
        src = self.win.lyrics.view
        lines, synced = src.lines()
        if lines:
            self.lyrics.set_lyrics(lines, synced)
        else:
            self.lyrics.set_status(src._status or "")
        self.lyrics.set_position(self.win.engine.position, self.win.engine.is_playing)

    def set_playing(self, playing: bool) -> None:
        self.b_play.setIcon(icon("pause" if playing else "play_arrow", "#000000"))
        self.lyrics.set_position(self.win.engine.position, playing)

    def toggle_lyrics(self) -> None:
        self.show_lyrics = not self.show_lyrics
        self._layout()
        self.update()

    def refresh_karaoke(self) -> None:
        self.karaoke.sync()
        self._layout()

    # ---- showing ------------------------------------------------------------------
    def open(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._anim.start()
        self._rta_timer.start()
        self.win.engine.set_rta(True)
        self._wake()

    def closeEvent(self, e) -> None:
        self._anim.stop()
        self._rta_timer.stop()
        self.rta = None
        self.closed.emit()
        super().closeEvent(e)

    # ---- layout & painting ------------------------------------------------------------
    def _geometry(self) -> dict:
        w, h = self.width(), self.height()
        margin = max(32, int(w * 0.06))
        karaoke = self.win.karaoke_state()[0]
        side = int(min(h * (0.36 if karaoke else 0.5), w * (0.24 if karaoke else 0.32)))
        has_lyrics = self.show_lyrics and bool(self.lyrics.lines()[0] or self.lyrics._status)
        if has_lyrics:
            cover = QRectF(margin, (h - side - 170) / 2, side, side)
            lyr = QRectF(cover.right() + margin * 1.1, h * 0.1, w - cover.right() - margin * 2.1, h * 0.72)
        else:
            cover = QRectF((w - side) / 2, (h - side - 170) / 2, side, side)
            lyr = QRectF()
        return {"cover": cover, "lyrics": lyr, "karaoke": karaoke, "margin": margin}

    def _layout(self) -> None:
        g = self._geometry()
        if g["lyrics"].isEmpty():
            self.lyrics.hide()
        else:
            self.lyrics.setGeometry(g["lyrics"].toRect())
            self.lyrics.show()
            px = int(max(26, min(56, self.height() / (16 if g["karaoke"] else 26))))
            dim = QColor(255, 255, 255, 150)
            self.lyrics.set_style(px, WHITE, dim, QColor(0, 0, 0, 0))
        self.controls.adjustSize()
        cw = self.controls.sizeHint().width()
        self.controls.setGeometry(int((self.width() - cw) / 2), self.height() - 150, cw, 80)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout()

    def _tick(self) -> None:
        self._phase += 0.006
        self._paint_bg()
        self.update()

    def _paint_bg(self) -> None:
        img = self._bg
        img.fill(self.base)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = img.width(), img.height()
        for i, c in enumerate(self.blobs):
            t = self._phase * (1 + 0.37 * i) + i * 2.1
            cx = W * (0.5 + 0.38 * math.sin(t * 0.9 + i))
            cy = H * (0.5 + 0.38 * math.cos(t * 0.7 + 1.3 * i))
            r = W * (0.55 + 0.1 * math.sin(t * 1.3))
            g = QRadialGradient(QPointF(cx, cy), r)
            col = QColor(c)
            col.setAlphaF(0.85)
            edge = QColor(c)
            edge.setAlphaF(0.0)
            g.setColorAt(0, col)
            g.setColorAt(1, edge)
            p.fillRect(img.rect(), g)
        shade = QColor(0, 0, 0, 70)                       # keep white text readable on any cover
        p.fillRect(img.rect(), shade)
        p.end()

    def _poll_rta(self) -> None:
        values = self.win.engine.rta()
        if values is None:
            self.rta = None
            return
        if self.rta is None or len(self.rta) != len(values):
            self.rta = list(values)
        else:
            self.rta = [v if v > o else max(v, o - 2.5) for v, o in zip(values, self.rta)]

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.rect(), self._bg)
        g = self._geometry()
        W, H = self.width(), self.height()
        # spectrum along the bottom
        if self.rta:
            n = len(self.rta)
            bw = W / n
            bar = QColor(255, 255, 255, 38)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bar)
            for i, v in enumerate(self.rta):
                h = max(0.0, min(1.0, (v + 80) / 70)) * H * 0.22
                p.drawRoundedRect(QRectF(i * bw + bw * 0.18, H - h, bw * 0.64, h), 4, 4)
        # cover with a soft shadow
        cover = g["cover"]
        for k in range(6):
            sh = QPainterPath()
            sh.addRoundedRect(cover.adjusted(-k * 3, -k * 3 + 12, k * 3, k * 3 + 12), 28 + k * 3, 28 + k * 3)
            p.fillPath(sh, QColor(0, 0, 0, 14))
        clip = QPainterPath()
        clip.addRoundedRect(cover, 28, 28)
        p.save()
        p.setClipPath(clip)
        if self.cover is not None and not self.cover.isNull():
            p.drawPixmap(cover, self.cover, QRectF(self.cover.rect()))
        else:
            p.fillRect(cover, QColor(255, 255, 255, 30))
            icon("music_note", "#FFFFFF").paint(p, cover.adjusted(cover.width() * 0.3, cover.height() * 0.3,
                                                               -cover.width() * 0.3, -cover.height() * 0.3).toRect())
        p.restore()
        # details
        t = self.track
        x, y, w = cover.left(), cover.bottom() + 28, max(cover.width(), 360)
        if g["lyrics"].isEmpty():
            x, w = (W - max(cover.width(), 520)) / 2, max(cover.width(), 520)
        align = Qt.AlignmentFlag.AlignHCenter if g["lyrics"].isEmpty() else Qt.AlignmentFlag.AlignLeft
        f = QFont(self.font())
        f.setPixelSize(max(22, int(H / 30)))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(WHITE)
        title = t["title"] if t else "Nothing playing"
        p.drawText(QRectF(x, y, w, f.pixelSize() * 1.4), align | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.TextElideMode.ElideRight, int(w)))
        y += f.pixelSize() * 1.45
        f2 = QFont(self.font())
        f2.setPixelSize(max(16, int(H / 48)))
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 200))
        if t:
            sub = f"{t['artist']}  ·  {t['album']}"
            p.drawText(QRectF(x, y, w, f2.pixelSize() * 1.5), align | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, int(w)))
        y += f2.pixelSize() * 2.2
        # progress
        dur = (t or {}).get("duration_ms", 0) or 0
        pos = self.win.engine.position
        track_r = QRectF(x, y, w, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawRoundedRect(track_r, 2, 2)
        if dur:
            p.setBrush(WHITE)
            p.drawRoundedRect(QRectF(x, y, w * max(0.0, min(1.0, pos / dur)), 4), 2, 2)
        self._progress_rect = track_r.adjusted(0, -10, 0, 10)
        f3 = QFont(self.font())
        f3.setPixelSize(13)
        p.setFont(f3)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(x, y + 10, w, 18), Qt.AlignmentFlag.AlignLeft, fmt(pos))
        p.drawText(QRectF(x, y + 10, w, 18), Qt.AlignmentFlag.AlignRight, fmt(dur))
        if g["karaoke"]:
            k = self.win.karaoke_state()
            chip = f"KARAOKE  ·  vocals −{round(k[1] * 100)}%" + (f"  ·  key {k[2]:+d}" if k[2] else "")
            p.setPen(QColor(255, 255, 255, 220))
            f3.setWeight(QFont.Weight.DemiBold)
            p.setFont(f3)
            p.drawText(QRectF(g["margin"], 28, W, 20), Qt.AlignmentFlag.AlignLeft, chip)

    # ---- interaction ---------------------------------------------------------------
    def _wake(self) -> None:
        self.controls.show()
        self.unsetCursor()
        self._idle.start()

    def _hide_controls(self) -> None:
        if self.controls.underMouse():
            self._idle.start()
            return
        self.controls.hide()
        self.setCursor(QCursor(Qt.CursorShape.BlankCursor))

    def mouseMoveEvent(self, e) -> None:
        self._wake()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        self._wake()
        rect = getattr(self, "_progress_rect", None)
        dur = (self.track or {}).get("duration_ms", 0)
        if rect is not None and dur and rect.contains(e.position()):
            frac = (e.position().x() - rect.left()) / max(1, rect.width())
            self.win.engine.seek(int(dur * max(0.0, min(1.0, frac))))

    def mouseDoubleClickEvent(self, _e) -> None:
        self.close()

    def keyPressEvent(self, e) -> None:
        k, eng = e.key(), self.win.engine
        self._wake()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close()
        elif k == Qt.Key.Key_Space:
            eng.toggle()
        elif k == Qt.Key.Key_Right:
            eng.seek(eng.position + 5000)
        elif k == Qt.Key.Key_Left:
            eng.seek(max(0, eng.position - 5000))
        elif k == Qt.Key.Key_N:
            eng.next()
        elif k == Qt.Key.Key_P:
            eng.previous()
        elif k == Qt.Key.Key_Up:
            self.win.vol.setValue(min(self.win.vol.maximum(), self.win.vol.value() + 5))
        elif k == Qt.Key.Key_Down:
            self.win.vol.setValue(max(0, self.win.vol.value() - 5))
        elif k == Qt.Key.Key_K:
            self.win.set_karaoke(on=not self.win.karaoke_state()[0])
        elif k == Qt.Key.Key_L:
            self.toggle_lyrics()
        else:
            super().keyPressEvent(e)
