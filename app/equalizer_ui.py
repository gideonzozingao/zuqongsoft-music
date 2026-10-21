"""Equalizer window: on/off switch, presets, 10 band sliders + pre-amp, live response curve."""
from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog, QDialogButtonBox, QFrame, QScrollArea,
                               QStackedWidget,
                               QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from . import libmpv
from .equalizer import BAND_LABELS, BANDS, CUSTOM, GAIN_RANGE, PREAMP_RANGE, PRESETS, EqState
from .theme import C

STEP = 0.1                                     # dB per slider step (arrow keys)


class Switch(QAbstractButton):
    """Material 3 switch (Qt has none)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(52, 32)

    def sizeHint(self) -> QSize:
        return QSize(52, 32)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QRectF(1, 1, 50, 30)
        p.setPen(Qt.PenStyle.NoPen if on else QPen(QColor(C["outline"]), 2))
        p.setBrush(QColor(C["primary"] if on else C["surface_container_highest"]))
        if not enabled:
            p.setOpacity(0.38)
        p.drawRoundedRect(track, 15, 15)
        d = 24 if on else 16
        cx = 36 if on else 16
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["on_primary"] if on else C["outline"]))
        p.drawEllipse(QPointF(cx, 16), d / 2, d / 2)


class ResponseCurve(QWidget):
    """Smooth line through the band gains (log-frequency x axis)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.gains = [0.0] * len(BANDS)
        self.active = False
        self.setMinimumHeight(110)

    def set_gains(self, gains: list[float], active: bool) -> None:
        self.gains, self.active = list(gains), active
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 10, -8, -10)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(self.rect()), 16, 16)
        p.fillPath(bg, QColor(C["surface_container"]))
        mid = r.center().y()
        p.setPen(QPen(QColor(C["outline_variant"]), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(r.left(), mid), QPointF(r.right(), mid))

        lo, hi = math.log10(BANDS[0]), math.log10(BANDS[-1])
        x_of = lambda f: r.left() + (math.log10(f) - lo) / (hi - lo) * r.width()     # noqa: E731
        y_of = lambda g: mid - g / GAIN_RANGE * (r.height() / 2)                       # noqa: E731
        pts = [QPointF(x_of(f), y_of(g)) for f, g in zip(BANDS, self.gains)]
        path = QPainterPath(QPointF(r.left(), pts[0].y()))
        path.lineTo(pts[0])
        for i in range(len(pts) - 1):            # Catmull-Rom -> cubic Bezier
            p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
            p3 = pts[min(i + 2, len(pts) - 1)]
            path.cubicTo(p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6, p2)
        path.lineTo(QPointF(r.right(), pts[-1].y()))

        colour = QColor(C["primary"] if self.active else C["outline"])
        fill = QPainterPath(path)
        fill.lineTo(r.right(), mid)
        fill.lineTo(r.left(), mid)
        fill.closeSubpath()
        tint = QColor(colour)
        tint.setAlphaF(0.18)
        p.fillPath(fill, tint)
        p.setPen(QPen(colour, 2.5))
        p.drawPath(path)
        p.setBrush(colour)
        p.setPen(Qt.PenStyle.NoPen)
        for pt in pts:
            p.drawEllipse(pt, 3.5, 3.5)


class BandSlider(QWidget):
    """Value label, vertical slider (double-click resets to 0 dB) and frequency label."""
    changed = Signal(float)

    class _Slider(QSlider):
        def mouseDoubleClickEvent(self, _e) -> None:
            self.setValue(0)

    def __init__(self, label: str, limit: float, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.value_lbl = QLabel("0", objectName="eqValue")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slider = self._Slider(Qt.Orientation.Vertical)
        steps = int(limit / STEP)
        self.slider.setRange(-steps, steps)
        self.slider.setPageStep(5)          # Page Up/Down: 0.5 dB
        self.slider.setMinimumHeight(170)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setToolTip("Drag, scroll or use the arrow keys. Double-click to reset.")
        name = QLabel(label, objectName="eqBand")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(name)
        self.slider.valueChanged.connect(self._moved)

    def _moved(self, v: int) -> None:
        db = v * STEP
        self.value_lbl.setText(f"{db:+.1f}".replace("+0.0", "0").replace("-0.0", "0"))
        self.changed.emit(db)

    def set_db(self, db: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(db / STEP))
        self.slider.blockSignals(False)
        v = self.slider.value() * STEP
        self.value_lbl.setText(f"{v:+.1f}".replace("+0.0", "0").replace("-0.0", "0"))


class _FetchEngine(QThread):
    """Downloads libmpv off the UI thread."""
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str, str)              # (installed path, error)

    def __init__(self, dest, parent=None) -> None:
        super().__init__(parent)
        self.dest = dest

    def run(self) -> None:
        try:
            path = libmpv.download(self.dest, self.progress.emit, log=self.status.emit)
            self.done.emit(str(path), "")
        except Exception as exc:
            self.done.emit("", str(exc))


class EqualizerDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self.setWindowTitle("Equalizer & Sound")
        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(lambda: self.win.save_eq())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(14)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.page_tabs = []
        for k, label in enumerate(("Equalizer", "Studio", "Sound")):
            b = QPushButton(label, objectName="segment")
            b.setCheckable(True)
            b.setProperty("seg", "first" if k == 0 else "last" if k == 2 else "mid")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=k: self.show_page(k))
            self.page_tabs.append(b)
            tabs.addWidget(b)
        tabs.addStretch(1)
        outer.addLayout(tabs)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        eq_page = QWidget()
        eq_scroll = QScrollArea()                 # tall on small screens: scroll like the other tabs
        eq_scroll.setWidgetResizable(True)
        eq_scroll.setFrameShape(QFrame.Shape.NoFrame)
        eq_scroll.setWidget(eq_page)
        self.pages.addWidget(eq_scroll)
        lay = QVBoxLayout(eq_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(QLabel("Equalizer", objectName="dialogTitle"), 1)
        self.state_lbl = QLabel("", objectName="muted")
        self.enable = Switch()
        head.addWidget(self.state_lbl)
        head.addSpacing(8)
        head.addWidget(self.enable)
        lay.addLayout(head)

        self.banner = QLabel("", objectName="banner")
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.banner)
        self.fetch_row = QWidget()
        fr = QHBoxLayout(self.fetch_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.btn_fetch = QPushButton("Download audio engine (about 32 MB)")
        self.btn_fetch.setProperty("kind", "filled")
        self.btn_restart = QPushButton("Restart now")
        self.btn_restart.setProperty("kind", "filled")
        self.btn_restart.hide()
        self.fetch_status = QLabel("", objectName="muted")
        self.fetch_status.setWordWrap(True)
        for b in (self.btn_fetch, self.btn_restart):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            fr.addWidget(b)
        fr.addWidget(self.fetch_status, 1)
        lay.addWidget(self.fetch_row)
        self._fetcher: _FetchEngine | None = None

        self.device_lbl = QLabel("", objectName="muted")
        lay.addWidget(self.device_lbl)

        presets = QHBoxLayout()
        presets.setSpacing(8)
        self.preset = QComboBox()
        self.preset.setMinimumWidth(200)
        self.btn_save = QPushButton("Save preset…")
        self.btn_delete = QPushButton("Delete")
        self.btn_reset = QPushButton("Reset")
        self.btn_import_tbl = QPushButton("Import…")
        self.btn_import_tbl.setToolTip("Load a Frequency (Hz) / Gain (dB) table")
        self.btn_export_tbl = QPushButton("Export…")
        self.btn_export_tbl.setToolTip("Save these 10 bands and the pre-amp as a Frequency (Hz) / Gain (dB) table")
        presets.addWidget(QLabel("Preset"))
        presets.addWidget(self.preset)
        presets.addStretch(1)
        for b in (self.btn_save, self.btn_delete, self.btn_import_tbl, self.btn_export_tbl, self.btn_reset):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            presets.addWidget(b)
        lay.addLayout(presets)

        self.curve = ResponseCurve()
        lay.addWidget(self.curve)

        bands = QHBoxLayout()
        bands.setSpacing(4)
        self.preamp = BandSlider("Pre-amp", PREAMP_RANGE)
        bands.addWidget(self.preamp)
        line = QFrame(objectName="eqDivider")
        line.setFixedWidth(1)
        bands.addSpacing(8)
        bands.addWidget(line)
        bands.addSpacing(8)
        self.bands = [BandSlider(lbl, GAIN_RANGE) for lbl in BAND_LABELS]
        for b in self.bands:
            bands.addWidget(b)
        lay.addLayout(bands)
        hint = QLabel("dB per band, ±12. A built-in limiter keeps boosted sound from distorting.",
                      objectName="muted")
        lay.addWidget(hint)

        opts = QVBoxLayout()
        opts.setSpacing(8)
        self.per_device = Switch()
        self.normalize = Switch()
        for sw, text in ((self.per_device, "Separate settings for each output device"),
                         (self.normalize, "Even out loudness between songs (uses ReplayGain tags)")):
            row = QHBoxLayout()
            row.addWidget(QLabel(text), 1)
            row.addWidget(sw)
            opts.addLayout(row)
        lay.addLayout(opts)

        # automatic EQ rules
        rules_title = QHBoxLayout()
        rt = QVBoxLayout()
        rt.setSpacing(1)
        rt.addWidget(QLabel("Automatic EQ", objectName="cardTitle"))
        rh = QLabel("Use a preset automatically for a genre, an album or a song. Add rules here or by "
                    "right-clicking a song.", objectName="muted")
        rh.setWordWrap(True)
        rt.addWidget(rh)
        rules_title.addLayout(rt, 1)
        lay.addLayout(rules_title)
        add = QHBoxLayout()
        self.rule_genre = QComboBox()
        self.rule_genre.setEditable(True)
        self.rule_genre.setMinimumWidth(160)
        self.rule_genre.lineEdit().setPlaceholderText("Genre")
        self.rule_preset = QComboBox()
        self.btn_rule_add = QPushButton("Add genre rule")
        add.addWidget(self.rule_genre, 1)
        add.addWidget(QLabel("→"))
        add.addWidget(self.rule_preset, 1)
        add.addWidget(self.btn_rule_add)
        lay.addLayout(add)
        self.rules_box = QVBoxLayout()
        self.rules_box.setSpacing(2)
        lay.addLayout(self.rules_box)
        self.btn_rule_add.clicked.connect(self._add_genre_rule)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        from .sound_ui import SoundPage
        self.sound = SoundPage(self.win, Switch)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.sound)
        from .studio_ui import StudioPage
        self.studio = StudioPage(self.win, Switch, self._save_timer)
        studio_scroll = QScrollArea()
        studio_scroll.setWidgetResizable(True)
        studio_scroll.setFrameShape(QFrame.Shape.NoFrame)
        studio_scroll.setWidget(self.studio)
        self.pages.addWidget(studio_scroll)
        self.pages.addWidget(scroll)
        self.resize(760, 760)
        outer.addWidget(buttons)
        self.show_page(0)

        self.btn_fetch.clicked.connect(self._fetch_engine)
        self.btn_restart.clicked.connect(self.win.restart_app)
        self.enable.toggled.connect(self._on_enable)
        self.preset.activated.connect(self._on_preset)
        self.btn_save.clicked.connect(self._save_preset)
        self.btn_delete.clicked.connect(self._delete_preset)
        self.btn_import_tbl.clicked.connect(self._import_table)
        self.btn_export_tbl.clicked.connect(self._export_table)
        self.btn_reset.clicked.connect(lambda: self._apply_gains(PRESETS["Flat"], "Flat", preamp=0.0))
        self.preamp.changed.connect(self._on_preamp)
        for i, b in enumerate(self.bands):
            b.changed.connect(lambda db, i=i: self._on_band(i, db))
        self.per_device.toggled.connect(self._on_per_device)
        self.normalize.toggled.connect(self._on_normalize)
        self.sync()

    # ---- state <-> widgets ---------------------------------------------------
    @property
    def state(self) -> EqState:
        return self.win.eq

    def _fill_rules(self) -> None:
        while self.rules_box.count():
            item = self.rules_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rules = self.win.auto_rules()
        if not rules:
            empty = QLabel("No rules yet.", objectName="muted")
            self.rules_box.addWidget(empty)
        kinds = {"genre": "Genre", "album": "Album", "song": "Song"}
        for r in rules:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            text = r["label"].replace("genre ", "").replace("the album ", "")
            h.addWidget(QLabel(f"{kinds[r['kind']]}: {text}  →  <b>{r['preset']}</b>"), 1)
            rm = QPushButton("Remove")
            rm.clicked.connect(lambda _c=False, r=r: self.win.add_auto_rule(r["kind"], r["key"], r["label"], None))
            h.addWidget(rm)
            self.rules_box.addWidget(w)
        self.rule_genre.clear()
        self.rule_genre.addItems(self.win.library.genres())
        self.rule_genre.setCurrentText("")
        self.rule_preset.clear()
        self.rule_preset.addItems(list(PRESETS) + sorted(self.win.eq_store.user_presets(), key=str.casefold))

    def _add_genre_rule(self) -> None:
        genre = self.rule_genre.currentText().strip()
        if genre:
            self.win.add_auto_rule("genre", genre.casefold(), f"genre {genre}", self.rule_preset.currentText())

    def show_page(self, k: int) -> None:
        self.pages.setCurrentIndex(k)
        for i, b in enumerate(self.page_tabs):
            b.setChecked(i == k)

    def sync(self) -> None:
        if hasattr(self, "rules_box"):
            self._fill_rules()
        if hasattr(self, "sound"):
            self.sound.sync()
        if hasattr(self, "studio"):
            self.studio.sync()
        st, store = self.state, self.win.eq_store
        supported = self.win.engine.supports_eq
        for w, v in ((self.enable, st.enabled and st.mode == "simple"), (self.per_device, store.per_device),
                     (self.normalize, store.normalize)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.preamp.set_db(st.preamp)
        for b, g in zip(self.bands, st.gains):
            b.set_db(g)
        self._fill_presets()
        for w in (self.enable, self.preset, self.btn_save, self.btn_reset, self.btn_import_tbl, self.btn_export_tbl,
                  self.preamp, *self.bands, self.normalize):
            w.setEnabled(supported)
        self._refresh()
        engine = self.win.engine
        self.banner.setVisible(not supported)
        can_fetch = (not supported and getattr(engine, "libmpv_missing", False)
                     and sys.platform == "win32" and libmpv.user_lib_dir() is not None)
        self.fetch_row.setVisible(can_fetch or self.btn_restart.isVisible())
        if not supported:
            if getattr(engine, "libmpv_missing", False):
                text = ("The equalizer needs the mpv audio engine (libmpv-2.dll), which isn't installed, "
                        "so music is playing through the basic engine."
                        + (" Download it here; the app restarts to use it." if can_fetch else ""))
            else:
                text = "The mpv audio engine is installed but couldn't start, so the equalizer is off."
            self.banner.setText(text + (f"\n\nDetails: {engine.fallback_reason}" if engine.fallback_reason else ""))
        dev = self.win.current_output_name()
        self.device_lbl.setText(f"Settings for: {dev}" if store.per_device and dev else "")
        self.device_lbl.setVisible(bool(self.device_lbl.text()))

    def _refresh(self) -> None:
        st = self.state
        simple_on = st.enabled and st.mode == "simple"
        self.state_lbl.setText("On" if simple_on else ("Studio EQ in use" if st.studio_on else "Off"))
        self.curve.set_gains(st.gains, simple_on and self.win.engine.supports_eq)
        self.btn_delete.setEnabled(self.win.engine.supports_eq and st.preset in self.win.eq_store.user_presets())

    def _fill_presets(self) -> None:
        self.preset.blockSignals(True)
        self.preset.clear()
        for name in PRESETS:
            self.preset.addItem(name, name)
        user = self.win.eq_store.user_presets()
        if user:
            self.preset.insertSeparator(self.preset.count())
            for name in sorted(user, key=str.casefold):
                self.preset.addItem(name, name)
        self.preset.insertSeparator(self.preset.count())
        self.preset.addItem(CUSTOM, CUSTOM)
        idx = self.preset.findData(self.state.preset)
        self.preset.setCurrentIndex(idx if idx >= 0 else self.preset.findData(CUSTOM))
        self.preset.blockSignals(False)

    def _changed(self, mark_custom: bool = True) -> None:
        st = self.state
        if mark_custom:
            st.preset = self._matching_preset(st.gains)
            idx = self.preset.findData(st.preset)
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(idx if idx >= 0 else self.preset.findData(CUSTOM))
            self.preset.blockSignals(False)
        self.win.clear_auto_eq()
        self.win.apply_eq()
        self._refresh()
        self._save_timer.start()

    def _matching_preset(self, gains: list[float]) -> str:
        every = {**{k: list(v) for k, v in PRESETS.items()}, **self.win.eq_store.user_presets()}
        for name, g in every.items():
            if all(abs(a - b) < 1e-6 for a, b in zip(gains, g)):
                return name
        return CUSTOM

    # ---- handlers ------------------------------------------------------------
    def _on_enable(self, on: bool) -> None:
        self.state.enabled = on
        if on:
            self.state.mode = "simple"          # the 10-band EQ takes over from Studio
        self._changed(mark_custom=False)

    def _on_band(self, i: int, db: float) -> None:
        self.state.gains[i] = db
        if not self.state.enabled or self.state.mode != "simple":   # touching a slider turns this EQ on
            self.state.enabled = True
            self.state.mode = "simple"
            self.enable.blockSignals(True)
            self.enable.setChecked(True)
            self.enable.blockSignals(False)
        self._changed()

    def _on_preamp(self, db: float) -> None:
        self.state.preamp = db
        self._changed(mark_custom=False)

    def _on_preset(self, _index: int) -> None:
        name = self.preset.currentData()
        if name == CUSTOM:
            return
        gains = PRESETS.get(name) or self.win.eq_store.user_presets().get(name)
        if gains is not None:
            self._apply_gains(gains, name)

    def _apply_gains(self, gains, name: str, preamp: float | None = None) -> None:
        st = self.state
        st.gains = [float(g) for g in gains]
        st.preset = name
        st.enabled = True
        st.mode = "simple"
        if preamp is not None:
            st.preamp = preamp
        self.sync()
        self._changed(mark_custom=False)

    def _save_preset(self) -> None:
        default = "" if self.state.preset in PRESETS or self.state.preset == CUSTOM else self.state.preset
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name", text=default)
        name = name.strip()
        if not ok or not name:
            return
        if name in PRESETS or name == CUSTOM:
            QMessageBox.information(self, "Save preset", f"“{name}” is a built-in preset. Pick another name.")
            return
        self.win.eq_store.save_user_preset(name, self.state.gains)
        self.state.preset = name
        self._fill_presets()
        self._changed(mark_custom=False)

    def _delete_preset(self) -> None:
        name = self.state.preset
        if name in self.win.eq_store.user_presets():
            self.win.eq_store.delete_user_preset(name)
            self.state.preset = CUSTOM
            self._fill_presets()
            self._changed(mark_custom=False)

    def _export_table(self) -> None:
        import os
        from PySide6.QtWidgets import QFileDialog
        from .equalizer import export_table
        name = self.state.preset if self.state.preset not in ("", CUSTOM) else "My"
        path, _ = QFileDialog.getSaveFileName(self, "Export equalizer",
                                              os.path.join(os.path.expanduser("~"), f"{name} EQ.txt"),
                                              "Equalizer table (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_table(self.state.gains, self.state.preamp))
            self.win.statusBar().showMessage(f"Exported to {path}", 6000)

    def _import_table(self) -> None:
        import os
        from PySide6.QtWidgets import QFileDialog
        from .equalizer import parse_table
        path, _ = QFileDialog.getOpenFileName(self, "Import equalizer", os.path.expanduser("~"),
                                              "Equalizer table (*.txt *.csv *.tsv);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                gains, preamp = parse_table(f.read())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import equalizer", f"Couldn't use that file.\n\n{exc}")
            return
        self._apply_gains(gains, self._matching_preset(gains), preamp=preamp)
        self.win.statusBar().showMessage(f"Imported {os.path.basename(path)}", 5000)

    def _on_per_device(self, on: bool) -> None:
        self.win.set_eq_per_device(on)
        self.sync()

    def _fetch_engine(self) -> None:
        if self._fetcher is not None and self._fetcher.isRunning():
            return
        self.btn_fetch.setEnabled(False)
        self.fetch_status.setText("Starting…")
        self._fetcher = _FetchEngine(libmpv.user_lib_dir(), self)
        self._fetcher.status.connect(self.fetch_status.setText)
        self._fetcher.progress.connect(lambda d, t: self.fetch_status.setText(
            f"Downloading… {d / 1e6:.1f} / {t / 1e6:.1f} MB" if t else f"Downloading… {d / 1e6:.1f} MB"))
        self._fetcher.done.connect(self._fetched)
        self._fetcher.start()

    def _fetched(self, path: str, error: str) -> None:
        if error:
            self.btn_fetch.setEnabled(True)
            self.btn_fetch.setText("Try again")
            self.fetch_status.setText(error)
            return
        self.btn_fetch.hide()
        self.btn_restart.show()
        self.fetch_status.setText("Installed. Restart the app to switch to the new engine.")

    def _on_normalize(self, on: bool) -> None:
        self.win.eq_store.normalize = on
        self.win.engine.set_normalize(on)
