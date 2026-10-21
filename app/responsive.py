"""Animated, responsive pane layout.

Panes sit side by side in a QHBoxLayout. Each layout change gives every pane a
plan: ("fixed", width), ("stretch",) or ("hidden",). PaneAnimator slides all panes
to their new widths together with one easing curve, so the row always fills the
window (no jumps or overlaps), then hands the stretch pane back to the layout.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QVariantAnimation, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

QWIDGETSIZE_MAX = (1 << 24) - 1


class PaneAnimator(QObject):
    finished = Signal()

    def __init__(self, layout: QHBoxLayout, panes: list[QWidget], parent=None) -> None:
        super().__init__(parent)
        self.layout, self.panes = layout, panes
        self.plan: dict[QWidget, tuple] = {}
        self._start: dict[QWidget, int] = {}
        self._end: dict[QWidget, int] = {}
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._finish)
        for p in panes:
            p.setMinimumWidth(1)     # explicit, so a pane's contents never block shrinking

    @property
    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def apply(self, plan: dict[QWidget, tuple], total: int, left_margin: int, animate: bool) -> None:
        """total = width of the widget holding the layout; left_margin = target left margin
        (animated too, so e.g. a sidebar sliding away leaves a proper gap without a jump)."""
        self._anim.stop()
        self.plan = plan
        m = self.layout.contentsMargins()
        self._m0, self._m1 = m.left(), left_margin
        if not animate:
            self._set_left(left_margin)
            self._finish()
            return
        spacing = self.layout.spacing()
        union = [p for p in self.panes if p.isVisible() or plan[p][0] != "hidden"]
        gaps = spacing * max(0, len(union) - 1)
        room0 = total - m.right() - self._m0 - gaps
        room = total - m.right() - self._m1 - gaps
        start = {p: (p.width() if p.isVisible() else 0) for p in self.panes}
        end = {}
        fixed = sum(plan[p][1] for p in union if plan[p][0] == "fixed")
        for p in self.panes:
            kind = plan[p][0]
            end[p] = plan[p][1] if kind == "fixed" else (0 if kind == "hidden" else 0)
        stretch = [p for p in union if plan[p][0] == "stretch"]
        for p in stretch:
            end[p] = max(1, (room - fixed) // max(1, len(stretch)))
        self._balance(start, union, room0)
        self._balance(end, union, room)
        for p in union:
            p.show()
        self._start, self._end = start, end
        self._anim.start()

    @staticmethod
    def _balance(widths: dict, union: list, room: int) -> None:
        """Make the widths add up to the room exactly (rounding, spacing of panes being shown)."""
        if not union:
            return
        delta = room - sum(widths[p] for p in union)
        biggest = max(union, key=lambda p: widths[p])
        widths[biggest] = max(0, widths[biggest] + delta)

    def _set_left(self, v: int) -> None:
        m = self.layout.contentsMargins()
        if m.left() != v:
            self.layout.setContentsMargins(v, m.top(), m.right(), m.bottom())

    def _step(self, t) -> None:
        self._set_left(round(self._m0 + (self._m1 - self._m0) * float(t)))
        for p in self.panes:
            if p.isVisible():
                a, b = self._start.get(p, 0), self._end.get(p, 0)
                p.setFixedWidth(max(0, round(a + (b - a) * float(t))))

    def _finish(self) -> None:
        self._set_left(self._m1 if hasattr(self, "_m1") else self.layout.contentsMargins().left())
        for p in self.panes:
            kind = self.plan.get(p, ("stretch",))
            if kind[0] == "hidden":
                p.hide()
                p.setMinimumWidth(1)
                p.setMaximumWidth(QWIDGETSIZE_MAX)
                self.layout.setStretchFactor(p, 0)
            elif kind[0] == "fixed":
                p.show()
                p.setFixedWidth(kind[1])
                self.layout.setStretchFactor(p, 0)
            else:
                p.show()
                p.setMinimumWidth(1)
                p.setMaximumWidth(QWIDGETSIZE_MAX)
                self.layout.setStretchFactor(p, 1)
        self.finished.emit()
