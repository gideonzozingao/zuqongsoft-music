"""Small widgets and helpers used across the main window."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QPushButton, QSlider, QStyle, QToolButton
from .. import theme
from ..theme import C, icon


class JumpSlider(QSlider):
    """Click anywhere to jump; `dragging` is True while the mouse is held."""
    seekRequested = Signal(int)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.dragging = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _value_at(self, event) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(event.position().x()), max(1, self.width()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_at(e))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            self.setValue(self._value_at(e))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.setValue(self._value_at(e))
            self.seekRequested.emit(self.value())
            e.accept()
        else:
            super().mouseReleaseEvent(e)

class VolumeSlider(JumpSlider):
    """Volume slider that can run past 100%: a tick marks 100% and the boost zone turns a warning colour."""

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.maximum() <= 100:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), 100, self.width() - 16) + 8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["on_surface_variant"]))
        p.drawRoundedRect(QRectF(x - 1, self.height() / 2 - 7, 2, 14), 1, 1)

def icon_button(name: str, tip: str, size: int = 40, icon_size: int = 24,
                role: str = "on_surface_variant") -> QToolButton:
    b = QToolButton()
    theme.bind(b, name, role)
    b.setIconSize(QSize(icon_size, icon_size))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b

def m3_button(text: str, kind: str = "", icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        theme.bind(b, icon_name, {"filled": "on_primary", "tonal": "on_secondary_container"}.get(kind, "primary"))
        b.setIconSize(QSize(18, 18))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b

def rounded_cover(src: QPixmap | None, side: int, radius: int, dpr: float) -> QPixmap:
    px = max(1, int(side * dpr))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    p.setClipPath(clip)
    if src is not None and not src.isNull():
        scaled = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
    else:
        p.fillRect(0, 0, px, px, QColor(C["surface_container_highest"]))
        m = int(px * 0.32)
        icon("music_note", C["outline"]).paint(p, QRect(m, m, px - 2 * m, px - 2 * m))
    p.end()
    out.setDevicePixelRatio(dpr)
    return out

def add_action(menu: QMenu, icon_name: str | None, text: str, fn, enabled: bool = True) -> QAction:
    act = menu.addAction(icon(icon_name), text) if icon_name else menu.addAction(text)
    act.triggered.connect(lambda _checked=False: fn())
    act.setEnabled(enabled)
    return act
