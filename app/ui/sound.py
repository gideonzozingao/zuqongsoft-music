"""Sound settings, loudness levelling and the background loudness scan."""
from __future__ import annotations

from PySide6.QtCore import QThread
from ..loudness_scan import LoudnessScanner
from ..sound_ui import sound_settings, speaker_settings


class SoundMixin:
    """Part of MainWindow: sound settings, loudness levelling and the background loudness scan."""

    def apply_sound_settings(self) -> None:
        """Push the Sound page settings to the engine and the volume slider."""
        s = sound_settings(self.settings)
        top = self.engine.max_volume if s["boost"] else 100
        if self.vol.value() > top:
            self.vol.setValue(top)
        self.vol.setMaximum(top)
        self.vol.update()
        self.engine.set_loudness(s["loudness"])
        self.engine.set_sound_options(s["exclusive"], s["hq_resample"], s["channels"])
        self.engine.set_speaker(speaker_settings(self.settings))
        level = self.settings.value("sound/level", False, type=bool) and self.engine.supports_eq
        self.engine.set_normalize(self.eq_store.normalize and not level)     # levelling replaces ReplayGain
        self._apply_track_level(self.engine.current_track)
        st = self.settings
        dj = st.value("dj/enabled", False, type=bool)
        trim = dj and st.value("dj/trim", True, type=bool)
        self.engine.set_dj(int(float(st.value("dj/crossfade", 6)) * 1000) if dj else 0, trim,
                           dj and st.value("dj/fades", True, type=bool), st.value("dj/keep_albums", True, type=bool))
        if level or trim:
            self.start_loudness_scan()          # measures loudness and silent intros/outros

    # ---- library loudness levelling ------------------------------------------------
    def _apply_track_level(self, t: dict | None) -> None:
        if not self.settings.value("sound/level", False, type=bool) or not self.engine.supports_eq:
            self.engine.set_track_gain(None)
            return
        target = float(self.settings.value("sound/level_target", -14.0))
        lufs = self.library.loudness_for(t["path"]) if t else None
        self.engine.set_track_gain(max(-20.0, min(12.0, target - lufs)) if lufs is not None else 0.0)

    def start_loudness_scan(self) -> None:
        if self._scanner is not None and self._scanner.isRunning():
            return
        if not self.engine.supports_eq or not self.library.unscanned_tracks():
            done, total = self.library.loudness_progress()
            self.loudness_status = f"All {total} songs measured" if total else ""
            return
        self._scanner = LoudnessScanner(self.library, self)
        self._scanner.progress.connect(self._scan_progress)
        self._scanner.measured.connect(self._scan_measured)
        self._scanner.start(QThread.Priority.LowPriority)

    def stop_loudness_scan(self) -> None:
        if self._scanner is not None and self._scanner.isRunning():
            self._scanner.requestInterruption()
            self._scanner.wait(3000)

    def _scan_progress(self, done: int, total: int) -> None:
        self.loudness_status = (f"Measured {done:,} of {total:,} songs" if done < total
                                else f"All {total:,} songs measured")

    def _scan_measured(self, path: str, _lufs) -> None:
        cur = self.engine.current_track
        if cur and cur["path"] == path:
            self._apply_track_level(cur)
