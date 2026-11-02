"""Themes: presets, light/dark, custom colours and live re-theming."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from .. import theme
from ..theme_picker import ThemeDialog


class ThemeMixin:
    """Part of MainWindow: themes: presets, light/dark, custom colours and live re-theming."""

    # ---- themes ------------------------------------------------------------
    def theme_state(self) -> tuple[str, str, str | None]:
        return self._theme_preset, self._theme_mode, self._theme_seed

    def set_theme(self, preset: str | None = None, mode: str | None = None, seed: str | None = None) -> None:
        if preset is not None:
            ok = preset in theme.PRESETS or preset in (theme.CUSTOM, theme.ALBUM)
            self._theme_preset = preset if ok else theme.DEFAULT_PRESET
            if self._theme_preset == theme.ALBUM:
                self._theme_from_art(self._cover_src, apply=False)
        if mode in theme.MODES:
            self._theme_mode = mode
        if seed:
            self._theme_seed = seed
        if self._theme_preset == theme.CUSTOM and not self._theme_seed:
            self._theme_preset = theme.DEFAULT_PRESET
        s = self.settings
        s.setValue("theme/preset", self._theme_preset)
        s.setValue("theme/mode", self._theme_mode)
        s.setValue("theme/seed", self._theme_seed or "")
        self._apply_theme()

    def show_theme_dialog(self) -> None:
        if self._theme_dialog is None:
            self._theme_dialog = ThemeDialog(self)
        self._theme_dialog.sync()
        self._theme_dialog.show()
        self._theme_dialog.raise_()
        self._theme_dialog.activateWindow()

    def _on_system_scheme(self, *_args) -> None:
        if self._theme_mode == "system" and not self._theming:
            self._apply_theme()
            if self._theme_dialog is not None and self._theme_dialog.isVisible():
                self._theme_dialog.sync()

    def _apply_theme(self) -> None:
        """Re-theme everything live: palette, style sheet and every colour-baked icon/pixmap."""
        if self._theming:
            return
        self._theming = True
        try:
            theme.apply(QApplication.instance(), self._theme_preset, self._theme_mode, self._theme_seed)
            theme.refresh_bound()
            self._refresh_nav()
            self._refresh_modes()
            self._on_playing(self.engine.is_playing)
            self._on_volume(self.vol.value())
            self._update_output_chip()
            self._refresh_eq_button()
            if self._mini is not None:
                self._mini.refresh_theme()
            if self._eq_dialog is not None:
                self._eq_dialog.update()
                self._eq_dialog.curve.update()
            self._set_cover(self._cover_src)
            self._set_album_label()
            for w in (self.stars, self.lyrics.view, self.queue_panel.view.viewport(), self.table.viewport()):
                w.update()
            if self.view["kind"] == "stats":
                self.stats_page.refresh()
            self.tracks.set_playing(self.engine.current_track["id"] if self.engine.current_track else None)
            self.grid.viewport().update()
        finally:
            self._theming = False
