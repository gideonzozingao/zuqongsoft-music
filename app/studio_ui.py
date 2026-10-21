"""Studio tab of the Equalizer window.

Graph: drag a point (left/right = frequency, up/down = gain), scroll on it for width (Q),
double-click empty space to add a band, right-click a point for type / on-off / delete,
hold Ctrl while pressing a point to hear only that band (solo; keep dragging to sweep).
Overlays: live spectrum, target curve, headphone measurement and the predicted result.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)

from . import studio as S
from . import theme
from .theme import C, icon
from .tools import MBAND, RTA_FREQS, TARGETS, interp_db, parse_curve_csv

DB_RANGE = 18.0
GRID_F = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
SLOTS = "ABCD"


def _f_label(f: float) -> str:
    return f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"


# ---- response graph ----------------------------------------------------------------------
class ResponseEditor(QWidget):
    changed = Signal()            # bands edited (live)
    selected = Signal(int)
    soloRequested = Signal(int)   # band index, or -1 to stop

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.state = S.StudioState()
        self.active = True
        self.sel = 0
        self._drag = -1
        self._soloing = False
        self._grid = S.StudioState.grid(260)
        self.rta: list[float] | None = None
        self.setMinimumHeight(270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setToolTip("Drag a point to change frequency and gain. Scroll on it to change the width (Q).\n"
                        "Double-click empty space to add a band; right-click a point for more.\n"
                        "Hold Ctrl and press a point to hear only that band (keep dragging to sweep).")

    # geometry
    def _plot(self) -> QRectF:
        return QRectF(self.rect()).adjusted(38, 12, -12, -26)

    def _x(self, f: float) -> float:
        r = self._plot()
        lo, hi = math.log10(S.F_MIN), math.log10(S.F_MAX)
        return r.left() + (math.log10(max(f, 1)) - lo) / (hi - lo) * r.width()

    def _f(self, x: float) -> float:
        r = self._plot()
        lo, hi = math.log10(S.F_MIN), math.log10(S.F_MAX)
        return max(S.F_MIN, min(S.F_MAX, 10 ** (lo + (x - r.left()) / r.width() * (hi - lo))))

    def _y(self, db: float) -> float:
        r = self._plot()
        return r.center().y() - max(-DB_RANGE * 1.4, min(DB_RANGE * 1.4, db)) / DB_RANGE * r.height() / 2

    def _db(self, y: float) -> float:
        r = self._plot()
        return max(-S.GAIN_LIMIT, min(S.GAIN_LIMIT, (r.center().y() - y) / (r.height() / 2) * DB_RANGE))

    def _node(self, i: int) -> QPointF:
        b = self.state.bands[i]
        if b.has_gain:
            return QPointF(self._x(b.freq), self._y(b.gain))
        return QPointF(self._x(b.freq), self._y(b.response_db([b.freq])[0] if b.type != "notch" else -DB_RANGE * 0.8))

    def _hit(self, pos: QPointF) -> int:
        best, dist = -1, 14.0
        for i in range(len(self.state.bands)):
            d = (self._node(i) - pos).manhattanLength()
            if d < dist:
                best, dist = i, d
        return best

    def set_state(self, st: S.StudioState, active: bool) -> None:
        self.state, self.active = st, active
        self.sel = max(0, min(self.sel, len(st.bands) - 1))
        self.update()

    def set_rta(self, values: list[float] | None) -> None:
        if values is None:
            self.rta = None
        elif self.rta is None:
            self.rta = list(values)
        else:                              # fast attack, slow fall: easier to read
            self.rta = [v if v > old else max(v, old - 2.0) for v, old in zip(values, self.rta)]
        self.update()

    # interaction
    def mousePressEvent(self, e) -> None:
        i = self._hit(e.position())
        if e.button() == Qt.MouseButton.RightButton and i >= 0:
            self.sel = i
            self.selected.emit(i)
            self._menu(i, e.globalPosition().toPoint())
            return
        if i >= 0:
            self.sel = i
            self._drag = i
            self.selected.emit(i)
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._soloing = True
                self.soloRequested.emit(i)
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag >= 0:
            b = self.state.bands[self._drag]
            b.freq = round(self._f(e.position().x()), 1)
            if b.has_gain:
                db = self._db(e.position().y())
                b.gain = round(db * 2) / 2 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else round(db, 1)
            self.changed.emit()
            self.update()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hit(e.position()) >= 0
                           else Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = -1
        if self._soloing:
            self._soloing = False
            self.soloRequested.emit(-1)

    def mouseDoubleClickEvent(self, e) -> None:
        if self._hit(e.position()) >= 0 or len(self.state.bands) >= S.MAX_BANDS:
            return
        self.state.bands.append(S.Band("peak", round(self._f(e.position().x()), 1),
                                       round(self._db(e.position().y()), 1), 1.0))
        self.sel = len(self.state.bands) - 1
        self.selected.emit(self.sel)
        self.changed.emit()
        self.update()

    def wheelEvent(self, e) -> None:
        i = self._hit(e.position())
        if i < 0:
            i = self.sel
        if not (0 <= i < len(self.state.bands)):
            return
        b = self.state.bands[i]
        b.q = round(max(S.Q_MIN, min(S.Q_MAX, b.q * (1.12 if e.angleDelta().y() > 0 else 1 / 1.12))), 3)
        self.sel = i
        self.selected.emit(i)
        self.changed.emit()
        self.update()

    def _menu(self, i: int, at) -> None:
        b = self.state.bands[i]
        m = QMenu(self)
        types = m.addMenu("Type")
        for key, (label, _f, _g) in S.TYPES.items():
            a = types.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == b.type)
            a.triggered.connect(lambda _c=False, k=key: self.set_type(i, k))
        on = m.addAction("Turn off" if b.enabled else "Turn on")
        on.triggered.connect(lambda: self.toggle(i))
        m.addSeparator()
        rm = m.addAction(icon("delete"), "Delete band")
        rm.triggered.connect(lambda: self.delete(i))
        m.exec(at)

    def set_type(self, i: int, kind: str) -> None:
        b = self.state.bands[i]
        b.type = kind
        b.q = S.DEFAULT_Q[kind]
        if not b.has_gain:
            b.gain = 0.0
        self.changed.emit()
        self.update()

    def toggle(self, i: int) -> None:
        self.state.bands[i].enabled = not self.state.bands[i].enabled
        self.changed.emit()
        self.update()

    def delete(self, i: int) -> None:
        if not self.state.bands:
            return
        del self.state.bands[i]
        self.sel = max(0, min(self.sel, len(self.state.bands) - 1))
        self.selected.emit(self.sel)
        self.changed.emit()
        self.update()

    # painting
    def _path(self, freqs, values) -> QPainterPath:
        path = QPainterPath(QPointF(self._x(freqs[0]), self._y(values[0])))
        for f, db in zip(freqs[1:], values[1:]):
            path.lineTo(self._x(f), self._y(db))
        return path

    def target_points(self) -> list:
        st = self.state
        if st.target == "custom":
            return st.target_custom
        return TARGETS[st.target][1] if st.target in TARGETS else []

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(self.rect()), 16, 16)
        p.fillPath(bg, QColor(C["surface_container"]))
        r = self._plot()
        small = QFont(self.font())
        small.setPixelSize(10)
        p.setFont(small)
        for f in GRID_F:
            x = self._x(f)
            p.setPen(QPen(QColor(C["outline_variant"]), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(C["on_surface_variant"]))
            p.drawText(QRectF(x - 20, r.bottom() + 4, 40, 16), Qt.AlignmentFlag.AlignCenter, _f_label(f))
        for db in (-18, -12, -6, 0, 6, 12, 18):
            y = self._y(db)
            p.setPen(QPen(QColor(C["outline"] if db == 0 else C["outline_variant"]), 1,
                          Qt.PenStyle.SolidLine if db == 0 else Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(C["on_surface_variant"]))
            p.drawText(QRectF(0, y - 8, r.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{db:+d}" if db else "0")
        p.save()
        p.setClipRect(r)
        # live spectrum (third-octave bars, -80..-10 dBFS mapped to the plot height)
        if self.rta:
            bar = QColor(C["primary"])
            bar.setAlphaF(0.22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bar)
            for f, v in zip(RTA_FREQS, self.rta):
                level = max(0.0, min(1.0, (v + 80) / 70))
                x0, x1 = self._x(f / 2 ** (1 / 6)) + 1, self._x(f * 2 ** (1 / 6)) - 1
                h = level * r.height()
                p.drawRoundedRect(QRectF(x0, r.bottom() - h, max(1.0, x1 - x0), h), 2, 2)
        eq = self.state.response(self._grid)
        # target / measurement / predicted result
        target = self.target_points()
        if target:
            p.setPen(QPen(QColor(C["on_surface"]), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path(self._grid, [interp_db(target, f) for f in self._grid]))
        if self.state.measurement:
            meas = [interp_db(self.state.measurement, f) for f in self._grid]
            p.setPen(QPen(QColor(C["outline"]), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path(self._grid, meas))
            p.setPen(QPen(QColor(C["tertiary"]), 2.2))
            p.drawPath(self._path(self._grid, [m + e for m, e in zip(meas, eq)]))
        # selected band's own curve
        if 0 <= self.sel < len(self.state.bands) and self.state.bands[self.sel].enabled:
            c = QColor(C["primary"])
            c.setAlphaF(0.45)
            p.setPen(QPen(c, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path(self._grid, self.state.bands[self.sel].response_db(self._grid)))
        # total EQ response (including pre-amp)
        path = self._path(self._grid, eq)
        colour = QColor(C["primary"] if self.active else C["outline"])
        fill = QPainterPath(path)
        fill.lineTo(r.right(), self._y(0))
        fill.lineTo(r.left(), self._y(0))
        fill.closeSubpath()
        tint = QColor(colour)
        tint.setAlphaF(0.14)
        p.fillPath(fill, tint)
        p.setPen(QPen(colour, 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()
        # nodes
        bold = QFont(self.font())
        bold.setPixelSize(11)
        bold.setWeight(QFont.Weight.Bold)
        p.setFont(bold)
        for i, b in enumerate(self.state.bands):
            pt = self._node(i)
            sel = i == self.sel
            if not b.enabled:
                fillc, text = QColor(C["surface_container_highest"]), QColor(C["outline"])
            elif sel:
                fillc, text = QColor(C["primary"]), QColor(C["on_primary"])
            else:
                fillc, text = QColor(C["primary_container"]), QColor(C["on_primary_container"])
            if sel:
                ring = QColor(C["tertiary"] if self._soloing else C["primary"])
                ring.setAlphaF(0.35)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ring)
                p.drawEllipse(pt, 16 if self._soloing else 15, 16 if self._soloing else 15)
            p.setPen(QPen(QColor(C["surface_container"]), 2))
            p.setBrush(fillc)
            p.drawEllipse(pt, 10, 10)
            p.setPen(text)
            p.drawText(QRectF(pt.x() - 10, pt.y() - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(i + 1))


# ---- level + correlation meters ----------------------------------------------------------------
class LevelBars(QWidget):
    """L/R level (RMS bar, peak tick) on a -60..0 dBFS scale, and stereo correlation (-1..+1)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.peak = [None, None]
        self.rms = [None, None]
        self.phase: float | None = None

    def set_values(self, lv: dict | None) -> None:
        if lv:
            self.peak, self.rms, self.phase = lv["peak"], lv["rms"], lv["phase"]
        else:
            self.peak, self.rms, self.phase = [None, None], [None, None], None
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(11)
        p.setFont(f)
        w = self.width() - 24
        x0 = 20
        pos = lambda db: x0 + max(0.0, min(1.0, ((db if db is not None else -90) + 60) / 60)) * w  # noqa: E731
        for row, label in enumerate("LR"):
            y = 4 + row * 18
            p.setPen(QColor(C["on_surface_variant"]))
            p.drawText(QRectF(0, y - 2, 16, 14), Qt.AlignmentFlag.AlignCenter, label)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["surface_container_highest"]))
            p.drawRoundedRect(QRectF(x0, y, w, 10), 5, 5)
            rms = self.rms[row]
            if rms is not None and rms > -90:
                p.setBrush(QColor(C["error"] if rms > -6 else C["primary"]))
                p.drawRoundedRect(QRectF(x0, y, max(4.0, pos(rms) - x0), 10), 5, 5)
            pk = self.peak[row]
            if pk is not None and pk > -90:
                p.setBrush(QColor(C["on_surface"]))
                p.drawRect(QRectF(pos(pk) - 1, y - 1, 2, 12))
        y = 44
        p.setPen(QColor(C["on_surface_variant"]))
        p.drawText(QRectF(0, y - 2, 16, 14), Qt.AlignmentFlag.AlignCenter, "±")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["surface_container_highest"]))
        p.drawRoundedRect(QRectF(x0, y, w, 10), 5, 5)
        p.setBrush(QColor(C["outline"]))
        p.drawRect(QRectF(x0 + w / 2 - 0.5, y - 2, 1, 14))
        if self.phase is not None:
            x = x0 + (self.phase + 1) / 2 * w
            p.setBrush(QColor(C["error"] if self.phase < 0 else C["tertiary"]))
            p.drawEllipse(QPointF(x, y + 5), 6, 6)


# ---- small control helpers --------------------------------------------------------------------
class ValueSlider(QWidget):
    """Label, slider and live value text. Shown value = slider value / scale."""
    changed = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, scale: float = 1.0, fmt: str = "{:g}", parent=None):
        super().__init__(parent)
        self.scale, self.fmt = scale, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        name = QLabel(label)
        name.setMinimumWidth(84)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(lo * scale), round(hi * scale))
        self.value = QLabel("", objectName="muted")
        self.value.setMinimumWidth(62)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value)
        self.slider.valueChanged.connect(self._moved)

    def _moved(self, v: int) -> None:
        self.value.setText(self.fmt.format(v / self.scale))
        self.changed.emit(v / self.scale)

    def set(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(v * self.scale))
        self.slider.blockSignals(False)
        self.value.setText(self.fmt.format(round(v * self.scale) / self.scale))


def _card(title: str, switch=None, hint: str = "") -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    card = QFrame(objectName="card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(6)
    head = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(1)
    col.addWidget(QLabel(title, objectName="cardTitle"))
    if hint:
        h = QLabel(hint, objectName="muted")
        h.setWordWrap(True)
        col.addWidget(h)
    head.addLayout(col, 1)
    if switch is not None:
        head.addWidget(switch, 0, Qt.AlignmentFlag.AlignTop)
    lay.addLayout(head)
    return card, lay, head


def _toggle(text: str, tip: str) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(True)
    b.setProperty("kind", "tonal")
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


# ---- the page ----------------------------------------------------------------------------------
class StudioPage(QWidget):
    def __init__(self, window, switch_cls, save_timer, parent=None) -> None:
        super().__init__(parent)
        self.win = window
        self._save = save_timer
        self._loading = False
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._last: dict | None = None
        self._last_push = 0.0
        self._Switch = switch_cls
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(QLabel("Studio EQ", objectName="cardTitle"))
        self.sub = QLabel("", objectName="muted")
        self.sub.setWordWrap(True)
        col.addWidget(self.sub)
        head.addLayout(col, 1)
        self.enable = switch_cls()
        self.enable.setToolTip("Use the Studio EQ instead of the 10-band equalizer")
        head.addWidget(self.enable, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.ab = _toggle("Compare A/B", "Hear the original and the EQ'd sound at the same loudness")
        self.btn_import = QPushButton("Import AutoEq…")
        self.btn_import.setToolTip("Load a headphone correction (ParametricEQ.txt from autoeq.app) or an "
                                   "Equalizer APO / REW parametric file")
        self.btn_export = QPushButton("Export…")
        self.btn_add = QPushButton("Add band")
        self.btn_reset = QPushButton("Reset")
        tools.addWidget(self.ab)
        tools.addStretch(1)
        for b in (self.btn_import, self.btn_export, self.btn_add, self.btn_reset):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tools.addWidget(b)
        lay.addLayout(tools)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setToolTip("Undo (Ctrl+Z)")
        self.btn_redo = QPushButton("Redo")
        self.btn_redo.setToolTip("Redo (Ctrl+Y)")
        row2.addWidget(self.btn_undo)
        row2.addWidget(self.btn_redo)
        row2.addSpacing(8)
        row2.addWidget(QLabel("Snapshots", objectName="muted"))
        self.slot_btns: dict[str, QPushButton] = {}
        for k in SLOTS:
            b = QPushButton(k, objectName="slot")
            b.setFixedWidth(44)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.clicked.connect(lambda _c=False, k=k: self._slot_clicked(k))
            b.customContextMenuRequested.connect(lambda _pos, k=k: self._slot_menu(k))
            self.slot_btns[k] = b
            row2.addWidget(b)
        row2.addStretch(1)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.spectrum = _toggle("Spectrum", "Show a live spectrum analyser behind the curve")
        self.target = QComboBox()
        self.target.addItem("No target", "none")
        for key, (label, _pts) in TARGETS.items():
            self.target.addItem(label, key)
        self.target.addItem("Imported target", "custom")
        self.target.addItem("Import target CSV…", "__import__")
        self.target.setToolTip("Reference curve to aim at (dashed)")
        self.btn_meas = QPushButton("Headphone measurement…")
        self.btn_meas.setToolTip("Import a frequency-response CSV of your headphones (e.g. from AutoEq's "
                                 "measurements) to see it, and the result with your EQ")
        row3.addWidget(self.spectrum)
        row3.addStretch(1)
        row3.addWidget(QLabel("Target", objectName="muted"))
        row3.addWidget(self.target)
        row3.addWidget(self.btn_meas)
        lay.addLayout(row3)

        self.ab_note = QLabel("", objectName="muted")
        self.ab_note.setWordWrap(True)
        lay.addWidget(self.ab_note)
        self.graph = ResponseEditor()
        lay.addWidget(self.graph)
        self.legend = QLabel("", objectName="muted")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.legend)

        band = QHBoxLayout()
        band.setSpacing(8)
        self.band_lbl = QLabel("Band 1", objectName="cardTitle")
        self.type = QComboBox()
        for key, (label, _f, _g) in S.TYPES.items():
            self.type.addItem(label, key)
        self.freq = QDoubleSpinBox(decimals=0, minimum=S.F_MIN, maximum=S.F_MAX, suffix=" Hz")
        self.gain = QDoubleSpinBox(decimals=1, minimum=-S.GAIN_LIMIT, maximum=S.GAIN_LIMIT, singleStep=0.5, suffix=" dB")
        self.q = QDoubleSpinBox(decimals=2, minimum=S.Q_MIN, maximum=S.Q_MAX, singleStep=0.05, prefix="Q ")
        self.band_on = QPushButton("On")
        self.band_on.setCheckable(True)
        self.band_on.setProperty("kind", "tonal")
        self.solo = QPushButton("Solo")
        self.solo.setProperty("kind", "tonal")
        self.solo.setToolTip("Hold to hear only this band (or hold Ctrl and press its point on the graph)")
        self.btn_del = QPushButton()
        theme.bind(self.btn_del, "delete")
        self.btn_del.setToolTip("Delete this band")
        for w in (self.band_lbl, self.type, self.freq, self.gain, self.q, self.band_on, self.solo, self.btn_del):
            band.addWidget(w)
        band.addStretch(1)
        lay.addLayout(band)

        pre = QHBoxLayout()
        pre.addWidget(QLabel("Pre-amp"))
        self.preamp = QDoubleSpinBox(decimals=1, minimum=-S.GAIN_LIMIT, maximum=12.0, singleStep=0.5, suffix=" dB")
        self.btn_auto = QPushButton("Auto")
        self.btn_auto.setToolTip("Lower the pre-amp so the loudest boost peaks at 0 dB")
        self.headroom = QLabel("", objectName="muted")
        pre.addWidget(self.preamp)
        pre.addWidget(self.btn_auto)
        pre.addWidget(self.headroom, 1)
        lay.addLayout(pre)

        # meters
        meters = QHBoxLayout()
        meters.setSpacing(12)
        mf, ml, mhead = _card("Loudness (LUFS)")
        self.btn_meter_reset = QPushButton("Reset")
        self.btn_meter_reset.setToolTip("Start the integrated reading again")
        mhead.addWidget(self.btn_meter_reset)
        grid = QHBoxLayout()
        self.meters: dict[str, QLabel] = {}
        for key, label, tip in (("M", "Momentary", "Last 0.4 s"), ("S", "Short-term", "Last 3 s"),
                                ("I", "Integrated", "Since the meter started"), ("TP", "True peak", "dBTP")):
            c = QVBoxLayout()
            c.setSpacing(0)
            v = QLabel("—", objectName="meterValue")
            v.setToolTip(tip)
            c.addWidget(v)
            c.addWidget(QLabel(label, objectName="muted"))
            grid.addLayout(c, 1)
            self.meters[key] = v
        ml.addLayout(grid)
        meters.addWidget(mf, 3)
        lf, ll, _h = _card("Levels")
        self.levels = LevelBars()
        self.levels.setToolTip("Left/right level (bar) and peak (tick). Bottom: stereo correlation; "
                               "a dot left of centre means out-of-phase content.")
        ll.addWidget(self.levels)
        meters.addWidget(lf, 2)
        lay.addLayout(meters)

        # crossfeed (kept from before) + tools
        lay.addWidget(QLabel("Tools", objectName="cardTitle"))
        self._build_tools(lay)

        self._meter_timer = QTimer(self, interval=100)
        self._meter_timer.timeout.connect(self._update_meters)
        self._tool_timer = QTimer(self, singleShot=True, interval=220)     # tools rebuild the chain: debounce
        self._tool_timer.timeout.connect(self._apply)

        self.enable.toggled.connect(self._on_enable)
        self.ab.toggled.connect(self._on_ab)
        self.btn_import.clicked.connect(self._import)
        self.btn_export.clicked.connect(self._export)
        self.btn_add.clicked.connect(self._add_band)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        for seq, fn in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
                        (QKeySequence("Ctrl+Y"), self.redo)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.spectrum.toggled.connect(self._on_spectrum)
        self.target.activated.connect(self._on_target)
        self.btn_meas.clicked.connect(self._measurement_menu)
        self.graph.changed.connect(self._graph_changed)
        self.graph.selected.connect(lambda _i: self._load_band())
        self.graph.soloRequested.connect(lambda i: self.win.engine.set_solo(None if i < 0 else i))
        self.type.activated.connect(lambda _i: self.graph.set_type(self.graph.sel, self.type.currentData()))
        for w in (self.freq, self.gain, self.q):
            w.valueChanged.connect(self._fields_changed)
        self.band_on.clicked.connect(lambda: self.graph.toggle(self.graph.sel))
        self.solo.pressed.connect(lambda: self.win.engine.set_solo(self.graph.sel) if self.st.bands else None)
        self.solo.released.connect(lambda: self.win.engine.set_solo(None))
        self.btn_del.clicked.connect(lambda: self.graph.delete(self.graph.sel))
        self.preamp.valueChanged.connect(self._preamp_changed)
        self.btn_auto.clicked.connect(lambda: self.preamp.setValue(self.st.auto_preamp()))
        self.btn_meter_reset.clicked.connect(lambda: self.win.engine.reset_meter())

    # ---- tools section ---------------------------------------------------------------
    def _build_tools(self, lay: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        lay.addLayout(grid)
        self._tw: dict[str, object] = {}

        def slider(card_lay, key, label, lo, hi, scale=1.0, fmt="{:g}"):
            s = ValueSlider(label, lo, hi, scale, fmt)
            s.changed.connect(lambda v, k=key: self._set_tool(k, v))
            card_lay.addWidget(s)
            self._tw[key] = s

        def switch(key):
            sw = self._Switch()
            sw.toggled.connect(lambda on, k=key: self._set_tool(k, on))
            self._tw[key] = sw
            return sw

        c, cl, _h = _card("Tone", hint="Tilts the whole sound around 1 kHz.")
        slider(cl, "tilt", "Warm ↔ bright", -6, 6, 10, "{:+.1f} dB")
        cl.addStretch(1)
        grid.addWidget(c, 0, 0)

        c, cl, _h = _card("Stereo")
        slider(cl, "width", "Width", 0, 200, 1, "{:.0f}%")
        slider(cl, "balance", "Balance", -100, 100, 1, "{:+.0f}")
        row = QHBoxLayout()
        for key, text, tip in (("mono", "Mono", "Listen in mono: checks how a mix collapses"),
                               ("swap", "Swap L/R", "Swap left and right"),
                               ("polarity", "Flip polarity", "Invert the polarity of both channels")):
            b = _toggle(text, tip)
            b.toggled.connect(lambda on, k=key: self._set_tool(k, on))
            self._tw[key] = b
            row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        grid.addWidget(c, 0, 1)

        c, cl, _h = _card("Compressor", switch("comp"), "Evens out loud and quiet parts.")
        slider(cl, "comp_threshold", "Threshold", -40, 0, 1, "{:.0f} dB")
        slider(cl, "comp_ratio", "Ratio", 1.5, 10, 10, "{:.1f}:1")
        slider(cl, "comp_attack", "Attack", 1, 200, 1, "{:.0f} ms")
        slider(cl, "comp_release", "Release", 20, 1000, 1, "{:.0f} ms")
        slider(cl, "comp_makeup", "Make-up", 0, 18, 1, "{:.0f} dB")
        grid.addWidget(c, 1, 0)

        c, cl, _h = _card("Multiband compressor", switch("mband"),
                          "Separate dynamics for bass (below 200 Hz), mids and treble (above 3 kHz).")
        amt = QComboBox()
        for key in MBAND:
            amt.addItem(key.capitalize(), key)
        amt.activated.connect(lambda _i: self._set_tool("mband_amount", amt.currentData()))
        self._tw["mband_amount"] = amt
        cl.addWidget(amt)
        cl.addSpacing(8)
        dh = QHBoxLayout()
        dcol = QVBoxLayout()
        dcol.setSpacing(1)
        dcol.addWidget(QLabel("De-esser", objectName="cardTitle"))
        dcol.addWidget(QLabel("Tames harsh “s” and “t” sounds.", objectName="muted"))
        dh.addLayout(dcol, 1)
        dh.addWidget(switch("deess"), 0, Qt.AlignmentFlag.AlignTop)
        cl.addLayout(dh)
        slider(cl, "deess_intensity", "Intensity", 0, 1, 100, "{:.0%}")
        slider(cl, "deess_freq", "Frequency", 0, 1, 100, "{:.0%}")
        grid.addWidget(c, 1, 1)

        c, cl, _h = _card("Bass enhancer", switch("bass"),
                          "Adds the impression of deep bass on earbuds and small speakers.")
        slider(cl, "bass_cutoff", "Below", 100, 250, 1, "{:.0f} Hz")
        slider(cl, "bass_strength", "Strength", 0.5, 3, 10, "{:.1f}")
        grid.addWidget(c, 2, 0)
        c, cl, _h = _card("Exciter", switch("exciter"), "Adds sparkle and air to dull recordings.")
        slider(cl, "exciter_amount", "Amount", 0, 3, 10, "{:.1f}")
        slider(cl, "exciter_freq", "Above", 2000, 12000, 1, "{:.0f} Hz")
        grid.addWidget(c, 2, 1)

        c, cl, _h = _card("Crossfeed", None, "For headphones: blends a little of each side into the other, "
                                             "like listening to speakers. Less tiring on hard-panned stereo.")
        self.crossfeed = self._Switch()
        _h.addWidget(self.crossfeed, 0, Qt.AlignmentFlag.AlignTop)
        self.xf_strength = ValueSlider("Amount", 0.05, 0.8, 100, "{:.0%}")
        cl.addWidget(self.xf_strength)
        self.crossfeed.toggled.connect(self._crossfeed_changed)
        self.xf_strength.changed.connect(self._crossfeed_changed)
        grid.addWidget(c, 3, 0)

        c, cl, _h = _card("Convolution", switch("conv"),
                          "Loads an impulse response (WAV): room correction from REW, headphone FIR "
                          "corrections, or reverb rooms.")
        row = QHBoxLayout()
        self.ir_name = QLabel("No impulse response loaded", objectName="muted")
        self.ir_name.setWordWrap(True)
        b = QPushButton("Load…")
        b.setToolTip("Load an impulse response (WAV)")
        b.clicked.connect(self._load_ir)
        row.addWidget(self.ir_name, 1)
        row.addWidget(b)
        cl.addLayout(row)
        grid.addWidget(c, 3, 1)

    def _set_tool(self, key: str, value) -> None:
        if self._loading:
            return
        setattr(self.st.tools, key, value)
        if key == "conv" and value and not self.st.tools.conv_path:
            self._load_ir()
            return
        self._tool_timer.start()

    def _crossfeed_changed(self, *_a) -> None:
        if self._loading:
            return
        self.st.crossfeed = self.crossfeed.isChecked()
        self.st.crossfeed_strength = round(self.xf_strength.slider.value() / 100, 2)
        self._apply()

    def _sync_tools(self) -> None:
        t = self.st.tools
        for key, w in self._tw.items():
            v = getattr(t, key)
            if isinstance(w, ValueSlider):
                w.set(v)
                continue
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(v)))
            else:
                w.setChecked(bool(v))
            w.blockSignals(False)
        self.crossfeed.blockSignals(True)
        self.crossfeed.setChecked(self.st.crossfeed)
        self.crossfeed.blockSignals(False)
        self.xf_strength.set(self.st.crossfeed_strength)
        self.ir_name.setText(t.conv_name or "No impulse response loaded")

    def _load_ir(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load impulse response", os.path.expanduser("~"),
                                              "Impulse response (*.wav);;All files (*)")
        t = self.st.tools
        if not path:
            if not t.conv_path:
                t.conv = False
                self._sync_tools()
            return
        folder = os.path.join(os.path.dirname(self.win.library.db_path), "impulses")
        os.makedirs(folder, exist_ok=True)
        with open(path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()[:16]
        dest = os.path.join(folder, f"{digest}.wav")         # a safe copy; the original may move
        shutil.copy2(path, dest)
        t.conv, t.conv_path, t.conv_name = True, dest, os.path.basename(path)
        self._sync_tools()
        self._apply()

    # ---- state -----------------------------------------------------------------
    @property
    def st(self) -> S.StudioState:
        return self.win.eq.studio

    def sync(self) -> None:
        self._loading = True
        eq = self.win.eq
        supported = self.win.engine.supports_eq
        self.enable.setChecked(eq.studio_on)
        self.preamp.setValue(self.st.preamp)
        self.graph.set_state(self.st, eq.studio_on)
        idx = self.target.findData(self.st.target)
        self.target.setCurrentIndex(idx if idx >= 0 else 0)
        self.spectrum.setChecked(self.win.settings.value("studio/spectrum", False, type=bool))
        self._sync_tools()
        for kind in (QPushButton, QDoubleSpinBox, QComboBox, QSlider):
            for w in self.findChildren(kind):
                if w is not self.btn_meter_reset:
                    w.setEnabled(supported)
        self.enable.setEnabled(supported)
        if self._last is None:
            self._last = self.st.to_dict()
        self._loading = False
        self._load_band()
        self._refresh_labels()

    def _load_band(self) -> None:
        self._loading = True
        bands = self.st.bands
        has = bool(bands)
        for w in (self.type, self.freq, self.gain, self.q, self.band_on, self.solo, self.btn_del):
            w.setEnabled(has and self.win.engine.supports_eq)
        if has:
            i = self.graph.sel
            b = bands[i]
            self.band_lbl.setText(f"Band {i + 1}")
            self.type.setCurrentIndex(self.type.findData(b.type))
            self.freq.setValue(b.freq)
            self.gain.setValue(b.gain)
            self.gain.setEnabled(b.has_gain and self.win.engine.supports_eq)
            self.q.setValue(b.q)
            self.band_on.setChecked(b.enabled)
            self.band_on.setText("On" if b.enabled else "Off")
        else:
            self.band_lbl.setText("No bands")
        self.btn_add.setEnabled(len(bands) < S.MAX_BANDS and self.win.engine.supports_eq)
        self._loading = False

    def _snapshots(self) -> dict:
        try:
            return json.loads(self.win.settings.value("studio/snapshots", "") or "{}")
        except (TypeError, ValueError):
            return {}

    def _refresh_labels(self) -> None:
        n = len(self.st.bands)
        state = "On" if self.win.eq.studio_on else "Off"
        extras = self.st.tools.active_names() + (["crossfeed"] if self.st.crossfeed else [])
        self.sub.setText(f"{state}  ·  {self.st.name}  ·  {n} band{'s' if n != 1 else ''}"
                         + (f"  ·  {', '.join(extras)}" if extras else ""))
        peak = max(self.st.response(S.StudioState.grid(300)))
        self.headroom.setText(f"Loudest point {peak:+.1f} dB" + ("  (the limiter catches the rest)" if peak > 0.05 else ""))
        on = self.ab.isChecked()
        self.ab.setText("Hearing: original" if on else "Compare A/B")
        self.ab_note.setText("Playing the original sound, level-matched to the Studio EQ, so loudness doesn't "
                             "sway your judgement. Click again to hear the EQ." if on else "")
        self.ab_note.setVisible(on)
        ok = self.win.engine.supports_eq
        self.btn_undo.setEnabled(bool(self._undo) and ok)
        self.btn_redo.setEnabled(bool(self._redo) and ok)
        snaps = self._snapshots()
        for k, b in self.slot_btns.items():
            filled = k in snaps
            b.setProperty("kind", "tonal" if filled else "")
            b.style().unpolish(b)
            b.style().polish(b)
            b.setToolTip(f"Snapshot {k}: {snaps[k].get('name', 'Studio')}. Click to recall; right-click to "
                         f"overwrite or clear." if filled else f"Empty. Click to save the current setup as {k}.")
        parts = [f"<span style='color:{C['primary']}'>━━</span> your EQ"]
        if self.st.measurement:
            parts += [f"<span style='color:{C['outline']}'>━━</span> {self.st.measurement_name or 'headphones'}",
                      f"<span style='color:{C['tertiary']}'>━━</span> headphones + EQ"]
        if self.graph.target_points():
            parts.append(f"<span style='color:{C['on_surface']}'>╍╍</span> target")
        if self.spectrum.isChecked():
            parts.append("bars: live spectrum")
        self.legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))

    def _record(self) -> None:
        """Undo history: rapid changes (a drag) are merged into one step."""
        now_state = self.st.to_dict()
        if self._last is not None and now_state != self._last:
            if time.monotonic() - self._last_push > 0.7:
                self._undo.append(self._last)
                del self._undo[:-100]
                self._redo.clear()
                self._last_push = time.monotonic()
        self._last = now_state

    def _restore_state(self, d: dict) -> None:
        self.win.eq.studio = S.StudioState.from_dict(d)
        self._last = d
        self.graph.sel = 0
        self.sync()
        self._apply(record=False)

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self.st.to_dict())
            self._restore_state(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self.st.to_dict())
            self._restore_state(self._redo.pop())

    def _apply(self, record: bool = True) -> None:
        if self._loading:
            return
        if record:
            self._record()
        self.win.clear_auto_eq()
        self.win.eq.preset = "Studio"
        self.win.apply_eq()
        if self.ab.isChecked():                       # keep the comparison level-matched
            self.win.engine.set_bypass(self.st.loudness_offset())
        self._refresh_labels()
        self.graph.update()
        self._save.start()

    # ---- snapshots -----------------------------------------------------------------
    def _slot_clicked(self, k: str) -> None:
        snaps = self._snapshots()
        if k in snaps:
            self._last_push = 0.0
            self._record()
            self._undo.append(self.st.to_dict())
            self._redo.clear()
            self._restore_state(snaps[k])
            self.win.statusBar().showMessage(f"Recalled snapshot {k}", 3000)
        else:
            self._save_slot(k)

    def _save_slot(self, k: str) -> None:
        snaps = self._snapshots()
        snaps[k] = self.st.to_dict()
        self.win.settings.setValue("studio/snapshots", json.dumps(snaps))
        self._refresh_labels()
        self.win.statusBar().showMessage(f"Saved the current setup as snapshot {k}", 3000)

    def _slot_menu(self, k: str) -> None:
        m = QMenu(self)
        m.addAction(f"Save current setup as {k}").triggered.connect(lambda: self._save_slot(k))
        clear = m.addAction("Clear")
        clear.setEnabled(k in self._snapshots())
        clear.triggered.connect(lambda: self._clear_slot(k))
        m.exec(self.slot_btns[k].mapToGlobal(self.slot_btns[k].rect().bottomLeft()))

    def _clear_slot(self, k: str) -> None:
        snaps = self._snapshots()
        snaps.pop(k, None)
        self.win.settings.setValue("studio/snapshots", json.dumps(snaps))
        self._refresh_labels()

    # ---- target & measurement --------------------------------------------------------
    def _on_target(self, _i: int) -> None:
        key = self.target.currentData()
        back = lambda: self.target.setCurrentIndex(max(0, self.target.findData(self.st.target)))  # noqa: E731
        if key == "__import__":
            path, _ = QFileDialog.getOpenFileName(self, "Import target curve", os.path.expanduser("~"),
                                                  "Frequency response (*.csv *.txt);;All files (*)")
            if not path:
                back()
                return
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as f:
                    self.st.target_custom = parse_curve_csv(f.read())
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Import target", f"Couldn't use that file.\n\n{exc}")
                back()
                return
            key = "custom"
            self.target.setCurrentIndex(self.target.findData("custom"))
        if key == "custom" and not self.st.target_custom:
            QMessageBox.information(self, "Target", "Import a target CSV first (the last item in this list).")
            back()
            return
        self.st.target = key
        self._refresh_labels()
        self.graph.update()
        self._save.start()

    def _measurement_menu(self) -> None:
        m = QMenu(self)
        m.addAction("Import measurement CSV…").triggered.connect(self._import_measurement)
        rm = m.addAction("Remove measurement")
        rm.setEnabled(bool(self.st.measurement))
        rm.triggered.connect(self._remove_measurement)
        m.exec(self.btn_meas.mapToGlobal(self.btn_meas.rect().bottomLeft()))

    def _import_measurement(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import headphone measurement", os.path.expanduser("~"),
                                              "Frequency response (*.csv *.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                self.st.measurement = parse_curve_csv(f.read())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import measurement", f"Couldn't use that file.\n\n{exc}")
            return
        self.st.measurement_name = os.path.splitext(os.path.basename(path))[0]
        if self.st.target == "none":
            self.st.target = "harman_oe"
            self.target.setCurrentIndex(self.target.findData("harman_oe"))
        self._refresh_labels()
        self.graph.update()
        self._save.start()

    def _remove_measurement(self) -> None:
        self.st.measurement, self.st.measurement_name = [], ""
        self._refresh_labels()
        self.graph.update()
        self._save.start()

    # ---- handlers -----------------------------------------------------------------
    def _on_enable(self, on: bool) -> None:
        if self._loading:
            return
        eq = self.win.eq
        eq.enabled = on
        if on:
            eq.mode = "studio"
        self.graph.set_state(self.st, eq.studio_on)
        if not on and self.ab.isChecked():
            self.ab.setChecked(False)
        self._apply()

    def _on_ab(self, on: bool) -> None:
        if on and not self.win.eq.studio_on:
            self.enable.setChecked(True)
        self.win.engine.set_bypass(self.st.loudness_offset() if on else None)
        self._refresh_labels()

    def _on_spectrum(self, on: bool) -> None:
        if self._loading:
            return
        self.win.settings.setValue("studio/spectrum", on)
        self.win.engine.set_rta(on and self.isVisible())
        if not on:
            self.graph.set_rta(None)
        self._refresh_labels()

    def _graph_changed(self) -> None:
        self._load_band()
        self._apply()

    def _fields_changed(self) -> None:
        if self._loading or not self.st.bands:
            return
        b = self.st.bands[self.graph.sel]
        b.freq, b.gain, b.q = self.freq.value(), self.gain.value() if b.has_gain else 0.0, self.q.value()
        self.graph.update()
        self._apply()

    def _preamp_changed(self, v: float) -> None:
        if self._loading:
            return
        self.st.preamp = v
        self.graph.update()
        self._apply()

    def _add_band(self) -> None:
        if len(self.st.bands) < S.MAX_BANDS:
            self.st.bands.append(S.Band("peak", 1000, 0, 1.0))
            self.graph.sel = len(self.st.bands) - 1
            self.graph.update()
            self._graph_changed()

    def _reset(self) -> None:
        keep = self.st
        new = S.StudioState()
        new.crossfeed, new.crossfeed_strength = keep.crossfeed, keep.crossfeed_strength
        new.target, new.target_custom = keep.target, keep.target_custom
        new.measurement, new.measurement_name = keep.measurement, keep.measurement_name
        self._record()
        self.win.eq.studio = new
        self.graph.sel = 0
        self.sync()
        self._apply()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import parametric EQ", os.path.expanduser("~"),
                                              "Parametric EQ (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                new = S.parse_parametric(f.read())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import parametric EQ", f"Couldn't use that file.\n\n{exc}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        for junk in (" ParametricEQ", "ParametricEQ", "_ParametricEQ"):
            name = name.replace(junk, "")
        new.name = name.strip(" _-") or "Imported"
        old = self.st
        new.crossfeed, new.crossfeed_strength, new.tools = old.crossfeed, old.crossfeed_strength, old.tools
        new.target, new.target_custom = old.target, old.target_custom
        new.measurement, new.measurement_name = old.measurement, old.measurement_name
        self._record()
        self.win.eq.studio = new
        self.win.eq.mode, self.win.eq.enabled = "studio", True
        self.graph.sel = 0
        self.sync()
        self._apply()
        skipped = getattr(new, "skipped", 0)
        self.win.statusBar().showMessage(f"Imported {new.name}: {len(new.bands)} bands"
                                         + (f" ({skipped} unsupported lines skipped)" if skipped else ""), 6000)

    def _export(self) -> None:
        parametric, table = "Parametric EQ (*.txt)", "10-band table: Frequency (Hz) / Gain (dB) (*.txt)"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export Studio EQ", os.path.join(os.path.expanduser("~"), f"{self.st.name} EQ.txt"),
            f"{parametric};;{table}")
        if not path:
            return
        if chosen == table:
            from .equalizer import TABLE_FREQS, export_table
            curve = self.st.response([float(f) for f in TABLE_FREQS], include_preamp=False)
            text = export_table([max(-12.0, min(12.0, round(g, 1))) for g in curve], self.st.preamp)
        else:
            text = S.export_parametric(self.st)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.win.statusBar().showMessage(f"Exported to {path}", 6000)

    # ---- meters ---------------------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.win.engine.set_meter(True)
        self.win.engine.set_rta(self.spectrum.isChecked())
        self._meter_timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._meter_timer.stop()
        self.win.engine.set_meter(False)
        self.win.engine.set_rta(False)
        self.win.engine.set_solo(None)
        self.graph.set_rta(None)
        if self.ab.isChecked():
            self.ab.setChecked(False)          # never leave the EQ bypassed by accident

    def _update_meters(self) -> None:
        eng = self.win.engine
        m = eng.meter()
        for key, lbl in self.meters.items():
            v = m.get(key) if m else None
            lbl.setText("—" if v is None or v <= -70 else f"{v:.1f}")
        self.levels.set_values(eng.levels())
        if self.spectrum.isChecked():
            self.graph.set_rta(eng.rta())
