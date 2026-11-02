"""Playing music and keeping the now-playing UI, volume and media keys in sync."""
from __future__ import annotations

import html
import random
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView, QMessageBox, QToolButton
from .. import theme
from ..engine import RepeatMode
from ..library import read_cover
from ..models import fmt
from ..theme import C, icon
from .widgets import rounded_cover


class PlaybackMixin:
    """Part of MainWindow: playing music and keeping the now-playing UI, volume and media keys in sync."""

    # ---- playback / UI sync --------------------------------------------------
    def _play_rows(self, rows: list[dict], start: int, shuffle: bool | None = None) -> None:
        self._play_source(self.view, rows, shuffle, start)

    def _play_source(self, view: dict, rows: list[dict], shuffle: bool | None, start: int | None = None) -> None:
        """Play `rows`; `view` is remembered so the queue can be rebuilt on next launch."""
        if not rows:
            return
        if shuffle is not None and shuffle != self.engine.shuffle:
            self._set_shuffle(shuffle)
        if start is None:
            start = random.randrange(len(rows)) if shuffle else 0
        self._radio_session = False
        same_view = view == self.view
        self._queue_source = self._source_for(view, None if same_view else "")
        if not same_view:
            # Rows came straight from the library (natural order), so record that.
            self._queue_source["sort"] = [0, Qt.SortOrder.AscendingOrder.value]
        self.engine.set_queue(rows, start)

    def _play_all(self, shuffle: bool) -> None:
        self._play_rows(self.tracks.tracks(), random.randrange(max(1, len(self.tracks.tracks())))
                        if shuffle else 0, shuffle)

    def _on_position(self, ms: int) -> None:
        if not self.seek.dragging:
            self.seek.setValue(ms)

    def _on_duration(self, ms: int) -> None:
        self.seek.setRange(0, max(0, ms))
        self.dur_lbl.setText(fmt(ms))
        if not self.seek.dragging:
            self.seek.setValue(self.engine.position)

    def _on_playing(self, playing: bool) -> None:
        self.btn_play.setIcon(icon("pause" if playing else "play_arrow", C["on_primary"]))

    def _on_track(self, t: dict) -> None:
        self.title_lbl.setText(t["title"])
        self.artist_lbl.setText(t["artist"])
        self._set_album_label()
        self.setWindowTitle(f"{t['title']} — {t['artist']}")
        data = read_cover(t["path"])
        pm = QPixmap()
        has_cover = bool(data) and pm.loadFromData(data)
        self._set_cover(pm if has_cover else None)
        self.mini_title.setText(t["title"])
        self.mini_artist.setText(t["artist"])
        self.lyrics.load(t)
        self._refresh_now_playing_stats()
        self._theme_from_art(pm if has_cover else None)
        self._apply_track_level(t)
        self._apply_auto_eq(t)
        art = pm if has_cover else None
        if self._fs is not None:
            self._fs.set_track(t, art)
        if self._mini is not None:
            self._mini.set_track(t, art)
        self.tray.set_track(t, self.sleep.remaining_text())
        self.media.update_track(t, data if has_cover else None)
        self.tracks.set_playing(t["id"])
        row = self.tracks.row_of(t["id"])
        if row >= 0 and self.stack.currentWidget() is self.table:
            self.table.scrollTo(self.tracks.index(row, 1), QAbstractItemView.ScrollHint.EnsureVisible)
        self._save_session()

    def _set_album_label(self) -> None:
        t = self.engine.current_track
        self.album_lbl.setText(
            f'<a href="album" style="color:{C["on_surface_variant"]};text-decoration:none">'
            f'{html.escape(t["album"])}</a>' if t else "")

    def _set_cover(self, pm: QPixmap | None) -> None:
        self._cover_src = pm
        dpr = self.screen().devicePixelRatio() if self.screen() else 1.0
        self.cover.setPixmap(rounded_cover(pm, self._cover_side, 28, dpr))
        self.mini_cover.setPixmap(rounded_cover(pm, 48, 10, dpr))

    def _theme_from_art(self, pm: QPixmap | None, apply: bool = True) -> None:
        """Material You: recolour the app from the cover (only when that theme is chosen)."""
        if self._theme_preset != theme.ALBUM or pm is None or pm.isNull():
            return
        found = theme.seed_from_image(pm.toImage())
        if not found or (found[0], found[1]) == (theme.art_seed, theme.art_vivid):
            return
        theme.art_seed, theme.art_vivid = found
        self.settings.setValue("theme/art_seed", f"{found[0]}|{int(found[1])}")
        if apply:
            self._apply_theme()

    def _set_shuffle(self, on: bool) -> None:
        if on != self.engine.shuffle:
            self.engine.set_shuffle(on)
        self._refresh_modes()

    def _cycle_repeat(self) -> None:
        self.engine.cycle_repeat()
        self._refresh_modes()

    def _refresh_modes(self) -> None:
        shuffle, mode = self.engine.shuffle, self.engine.repeat
        self._set_toggle(self.btn_shuffle, "shuffle", shuffle)
        self._set_toggle(self.btn_repeat, "repeat_one" if mode == RepeatMode.ONE else "repeat",
                         mode != RepeatMode.OFF)
        self.btn_repeat.setToolTip(f"Repeat: {mode.name.lower()} (Ctrl+R)")
        self.act_shuffle.blockSignals(True)
        self.act_shuffle.setChecked(shuffle)
        self.act_shuffle.blockSignals(False)

    @staticmethod
    def _set_toggle(btn: QToolButton, name: str, on: bool) -> None:
        btn.setIcon(icon(name, C["on_secondary_container"] if on else C["on_surface_variant"]))
        btn.setProperty("active", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _boost_allowed_now(self, v: int) -> bool:
        """One-time hearing warning the first time you go past 100% on headphones or Bluetooth."""
        out = self._current_out
        if v <= 100 or not (out and out.personal) or self.settings.value("sound/boost_warned", False, type=bool):
            return True
        self.vol.dragging = False
        self.vol.blockSignals(True)
        self.vol.setValue(100)
        self.vol.blockSignals(False)
        self.engine.set_volume(100)
        if not getattr(self, "_asking_boost", False):
            self._asking_boost = True
            QTimer.singleShot(0, lambda: self._ask_boost(v))
        return False

    def _ask_boost(self, v: int) -> None:
        answer = QMessageBox.warning(
            self, "Volume above 100%",
            f"You're listening on {self._current_out.name if self._current_out else 'headphones'}.\n\n"
            "Going above 100% can be very loud on headphones, and listening at high volume for long "
            "periods can permanently damage your hearing.\n\nGo above 100% anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        self._asking_boost = False
        if answer == QMessageBox.StandardButton.Yes:
            self.settings.setValue("sound/boost_warned", True)
            self.vol.setValue(v)

    def _on_volume(self, v: int) -> None:
        if not self._boost_allowed_now(v):
            return
        boosted = v > 100
        if self.vol.property("boost") != boosted:
            self.vol.setProperty("boost", boosted)
            self.vol.style().unpolish(self.vol)
            self.vol.style().polish(self.vol)
        if boosted and self.vol.isVisible():
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(self.vol.mapToGlobal(self.vol.rect().topRight()), f"{v}% (boost)", self.vol)
        self.engine.set_volume(v)
        name = "volume_off" if v == 0 else ("volume_down" if v < 50 else "volume_up")
        self.btn_vol.setIcon(icon(name))
        self.btn_vol.setToolTip(("Unmute" if v == 0 else "Mute") + f" (volume {v}%, scroll to change)")

    def _toggle_mute(self) -> None:
        if self.vol.value() > 0:
            self._unmute_volume = self.vol.value()
            self.vol.setValue(0)
        else:
            self.vol.setValue(self._unmute_volume or 70)

    def _set_hotkeys(self, on: bool) -> None:
        self.media.set_hotkeys_enabled(on)
        if on and self.media.hotkey_labels:
            self.act_hotkeys.setToolTip(", ".join(self.media.hotkey_labels))

    def _on_media_command(self, cmd: str) -> None:
        e = self.engine
        actions = {
            "play": e.play, "pause": e.pause, "toggle": e.toggle, "stop": e.pause,
            "next": e.next, "previous": e.previous,
            "vol_up": lambda: self.vol.setValue(min(self.vol.maximum(), self.vol.value() + 5)),
            "vol_down": lambda: self.vol.setValue(max(0, self.vol.value() - 5)),
        }
        fn = actions.get(cmd)
        if fn:
            fn()
