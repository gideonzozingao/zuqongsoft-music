"""DJ mode, Song Radio, karaoke, full screen, mini player, tray and sleep timer."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QPushButton
from .. import theme
from ..fullscreen import FullScreenView, KaraokeDialog
from ..mini import MiniPlayer
from .constants import SONGS


class ExtrasMixin:
    """Part of MainWindow: dJ mode, Song Radio, karaoke, full screen, mini player, tray and sleep timer."""

    # ---- DJ mode, autoplay, radio -----------------------------------------------------
    def _toggle_dj(self, on: bool) -> None:
        self.settings.setValue("dj/enabled", on)
        self.apply_sound_settings()
        xf = float(self.settings.value("dj/crossfade", 6))
        self.statusBar().showMessage(f"DJ mode on: {xf:g} s crossfades, smooth fades" if on else "DJ mode off", 4000)
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    def _set_autoplay(self, on: bool) -> None:
        self.settings.setValue("playback/autoplay", on)
        for w in (self.act_autoplay, self.queue_panel.btn_autoplay):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        if on:
            self._radio_top_up()

    def start_radio(self, seed: dict) -> None:
        picks = self.radio.pick([seed], {seed["path"]}, 9)
        self._radio_session = False
        self._queue_source = dict(SONGS)
        self.engine.set_queue([seed] + picks, 0)
        self._radio_session = True
        self.statusBar().showMessage(f"Radio based on “{seed['title']}”: songs that fit will keep coming", 5000)
        self._set_np_tab(2)

    def _radio_top_up(self) -> None:
        """Keep at least three songs coming when autoplay or a radio is on."""
        if not (self._radio_session or self.settings.value("playback/autoplay", False, type=bool)):
            return
        cur = self.engine.current_track
        upcoming = [t for t, _u in self.engine.upcoming_items()]
        if cur is None or len(upcoming) >= 3:
            return
        seeds = [cur] + upcoming[-3:]
        exclude = {t["path"] for t in self.engine._queue}
        picks = self.radio.pick(seeds, exclude, 8)
        if picks:
            self.engine.extend(picks)

    # ---- karaoke ------------------------------------------------------------------------
    def karaoke_state(self) -> tuple[bool, float, int]:
        return self._karaoke[0], self._karaoke[1], self._karaoke[2]

    def set_karaoke(self, on: bool | None = None, vocal: float | None = None, key: int | None = None) -> None:
        if not self.engine.supports_eq:
            self.statusBar().showMessage("Karaoke needs the mpv audio engine (see Playback > Equalizer).", 6000)
            return
        k = self._karaoke
        if on is not None:
            k[0] = on
        if vocal is not None:
            k[1] = max(0.0, min(1.0, vocal))
            self.settings.setValue("karaoke/vocal", k[1])
        if key is not None:
            k[2] = max(-6, min(6, int(key)))
            self.settings.setValue("karaoke/key", k[2])
        self.engine.set_karaoke(k[1] if k[0] else 0.0, k[2] if k[0] else 0)
        for w in (self._karaoke_dialog.controls if self._karaoke_dialog else None,):
            if w is not None:
                w.sync()
        if self._fs is not None:
            self._fs.refresh_karaoke()

    def show_karaoke(self) -> None:
        if self._karaoke_dialog is None:
            self._karaoke_dialog = KaraokeDialog(self)
        self._karaoke_dialog.controls.sync()
        self._karaoke_dialog.show()
        self._karaoke_dialog.raise_()

    # ---- full screen, mini player, tray ---------------------------------------------------
    def show_fullscreen(self) -> None:
        if self._fs is None:
            self._fs = FullScreenView(self)
            self._fs.closed.connect(self._fullscreen_closed)
        t = self.engine.current_track
        self._fs.set_track(t, self._cover_src)
        self.lyrics.set_active(True)                 # look lyrics up online while full screen
        self._fs.sync_lyrics()
        self._fs.set_playing(self.engine.is_playing)
        self._fs.refresh_karaoke()
        self._fs.open()

    def _fullscreen_closed(self) -> None:
        self.lyrics.set_active(self.np_stack.currentIndex() == 1)
        studio_open = self._eq_dialog is not None and self._eq_dialog.isVisible() \
            and self._eq_dialog.pages.currentIndex() == 1 and self._eq_dialog.studio.spectrum.isChecked()
        self.engine.set_rta(studio_open)

    def show_mini(self) -> None:
        if self._mini is None:
            self._mini = MiniPlayer(self)
            self._mini.expandRequested.connect(self.show_main)
            pos = self.settings.value("mini/pos")
            if pos is not None:
                self._mini.move(pos)
            else:
                geo = self.screen().availableGeometry()
                self._mini.move(geo.right() - self._mini.width() - 24, geo.bottom() - self._mini.height() - 24)
        self._mini.set_track(self.engine.current_track, self._cover_src)
        self._mini.set_playing(self.engine.is_playing)
        self._mini.show()
        self._mini.raise_()
        self.hide()

    def show_main(self) -> None:
        if self._mini is not None:
            self._mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_main(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_main()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()
        QApplication.quit()

    def _set_keep_in_tray(self, on: bool) -> None:
        self.settings.setValue("ui/keep_in_tray", on)

    def _on_sleep_status(self, text: str) -> None:
        if not hasattr(self, "sleep_lbl"):
            self.sleep_lbl = QPushButton("")
            self.sleep_lbl.setToolTip("Sleep timer: click to cancel")
            theme.bind(self.sleep_lbl, "bedtime", "primary")
            self.sleep_lbl.clicked.connect(lambda: self.sleep.cancel())
            self.statusBar().addPermanentWidget(self.sleep_lbl)
        self.sleep_lbl.setVisible(bool(text))
        self.sleep_lbl.setText(f"Sleep {text}" if text.startswith("after") else f"Sleep in {text}")
        self.tray.set_track(self.engine.current_track, text)

    def _on_position_extra(self, ms: int) -> None:
        if self._fs is not None and self._fs.isVisible():
            self._fs.lyrics.set_position(ms, self.engine.is_playing)

    def _on_playing_extra(self, playing: bool) -> None:
        if self._fs is not None:
            self._fs.set_playing(playing)
        if self._mini is not None:
            self._mini.set_playing(playing)
