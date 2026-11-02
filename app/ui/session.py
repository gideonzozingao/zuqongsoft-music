"""Settings, saving and restoring the session, and shutting down."""
from __future__ import annotations

import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from ..engine import RepeatMode
from ..models import COL_TIME, fmt, sort_rows
from .constants import SONGS


class SessionMixin:
    """Part of MainWindow: settings, saving and restoring the session, and shutting down."""

    # ---- settings / session ------------------------------------------------
    def _folders(self) -> list[str]:
        v = self.settings.value("folders", [])
        if isinstance(v, str):
            v = [v]
        return [str(p) for p in (v or [])]

    def _restore(self) -> None:
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.vol.setValue(min(100, int(self.settings.value("volume", 70))))   # never start boosted
        self._on_volume(self.vol.value())
        self.engine.set_shuffle(self.settings.value("shuffle", False, type=bool))
        self.engine.set_repeat(RepeatMode(int(self.settings.value("repeat", 0))))
        self._refresh_modes()

        col = int(self.settings.value("sort/column", 0))
        order = Qt.SortOrder(int(self.settings.value("sort/order", 0)))
        self.table.horizontalHeader().setSortIndicator(col if 0 <= col <= COL_TIME else 0, order)
        self.table.setSortingEnabled(True)

        if self.media.hotkeys_supported:
            self.act_hotkeys.setChecked(self.settings.value("hotkeys", True, type=bool))

        self._pinned_id = str(self.settings.value("audio/device", "") or "")
        self._pinned_name = str(self.settings.value("audio/device_name", "") or "")
        self._auto_switch = self.settings.value("audio/auto_switch", True, type=bool)
        self._pause_on_disconnect = self.settings.value("audio/pause_on_disconnect", True, type=bool)
        self._set_np_tab(int(self.settings.value("ui/np_tab", 0) or 0) % 3)
        for act, key in ((self.act_dj, "dj/enabled"), (self.act_autoplay, "playback/autoplay"),
                         (self.act_tray, "ui/keep_in_tray")):
            act.blockSignals(True)
            act.setChecked(self.settings.value(key, False, type=bool))
            act.blockSignals(False)
        self.queue_panel.btn_autoplay.blockSignals(True)
        self.queue_panel.btn_autoplay.setChecked(self.act_autoplay.isChecked())
        self.queue_panel.btn_autoplay.blockSignals(False)
        self._apply_output()
        self.engine.set_normalize(self.eq_store.normalize)
        self.apply_sound_settings()
        self._load_eq_for_device(force=True)
        if self.engine.fallback_reason:
            self.statusBar().showMessage("Basic audio engine in use, so the equalizer is off. "
                                         "Open Playback > Equalizer for details.", 10000)

    def _source_for(self, view: dict, search: str | None = None) -> dict:
        col, order = self.tracks.sort_state()
        src = dict(view)
        src["search"] = self.search.text() if search is None else search
        src["sort"] = [col, order.value]
        return src

    def _save_session(self) -> None:
        t = self.engine.current_track
        if t is None:
            return
        s = self.settings
        s.setValue("session/path", t["path"])
        s.setValue("session/position", int(self.engine.position))
        s.setValue("session/source", json.dumps(self._queue_source))

    def _restore_session(self) -> None:
        """Reload last session's queue and track, paused at the saved position."""
        path = self.settings.value("session/path", "")
        if not path:
            return
        try:
            src = json.loads(self.settings.value("session/source", "") or "{}")
        except (TypeError, ValueError):
            src = {}
        if src.get("kind") not in ("songs", "album", "playlist", "smart"):
            src = dict(SONGS)
        try:
            col, order = (int(x) for x in src.get("sort", [0, 0]))
            order = Qt.SortOrder(order)
        except (TypeError, ValueError):
            col, order = 0, Qt.SortOrder.AscendingOrder
        rows = sort_rows(self._rows_for(src, src.get("search", "")), col, order)
        idx = next((i for i, t in enumerate(rows) if t["path"] == path), -1)
        if idx < 0:                      # source gone (playlist deleted, etc.): fall back to library
            src = self._source_for(SONGS, "")
            rows = self.library.search("")
            idx = next((i for i, t in enumerate(rows) if t["path"] == path), -1)
        if idx < 0:
            return
        self._queue_source = src
        pos = int(self.settings.value("session/position", 0) or 0)
        self.engine.set_queue(rows, start=idx, autoplay=False, position_ms=pos)
        self.statusBar().showMessage(f"Ready to resume “{rows[idx]['title']}” at {fmt(pos)}", 6000)

    def closeEvent(self, event) -> None:
        if (not self._quitting and self.settings.value("ui/keep_in_tray", False, type=bool)
                and self.tray.tray.isVisible()):
            event.ignore()                       # keep playing; the tray icon brings the window back
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.tray.showMessage("Zuqongsoft Music", "Still playing here. Right-click the icon for "
                                           "controls, or choose Quit.", QIcon(), 4000)
            return
        for w in (self._fs, self._mini):
            if w is not None:
                w.close()
        self.tray.tray.hide()
        self._save_session()                 # before stop() resets the position
        self.tracker.finish(closing=True)
        self.stop_loudness_scan()
        self.lyrics.shutdown()
        if self.scanner is not None and self.scanner.isRunning():
            self.scanner.requestInterruption()
            self.scanner.wait(5000)
        self.covers.shutdown()
        self.media.shutdown()
        self.save_eq()
        self.engine.shutdown()
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("volume", min(100, self.vol.value()))
        s.setValue("shuffle", self.engine.shuffle)
        s.setValue("repeat", self.engine.repeat.value)
        s.setValue("hotkeys", self.act_hotkeys.isChecked())
        s.setValue("audio/auto_switch", self._auto_switch)
        s.setValue("audio/pause_on_disconnect", self._pause_on_disconnect)
        super().closeEvent(event)
