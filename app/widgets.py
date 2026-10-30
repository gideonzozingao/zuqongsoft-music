"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSlider, QStyle


class JumpSlider(QSlider):
    """Click anywhere to jump; `dragging` is True while the mouse is held."""
    seekRequested = Signal(int)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.dragging = False

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
