"""Responsive wide / medium / compact layout and the window's event filter."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from .. import theme
from ..models import COL_RATING
from .constants import DRAWER_W, MEDIUM_MIN, NP_W, WIDE_MIN


class ResponsiveMixin:
    """Part of MainWindow: responsive wide / medium / compact layout and the window's event filter."""

    # ---- responsive layout -------------------------------------------------
    def _mode_for(self, width: int) -> str:
        return "wide" if width >= WIDE_MIN else "medium" if width >= MEDIUM_MIN else "compact"

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._check_mode()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._check_mode()

    def _check_mode(self) -> None:
        mode = self._mode_for(self.width())
        if mode == self._mode:
            return
        first = self._mode is None
        self._mode = mode
        self._drawer_open = False              # the sidebar always starts folded away when space shrinks
        self._apply_layout(animate=not first and self.isVisible())

    def _plan(self) -> dict:
        d, c, n = self.drawer_w, self.content_w, self.np_w
        hidden, stretch = ("hidden",), ("stretch",)
        if self._mode == "wide":
            return {d: ("fixed", DRAWER_W), c: stretch, n: ("fixed", NP_W)}
        drawer = ("fixed", DRAWER_W) if self._drawer_open else hidden
        if self._mode == "medium":
            return {d: drawer, c: stretch, n: ("fixed", NP_W)}
        if self._screen == "library":
            return {d: drawer, c: stretch, n: hidden}
        return {d: hidden, c: hidden, n: stretch}

    def _apply_layout(self, animate: bool = True) -> None:
        plan = self._plan()
        left = 0 if plan[self.drawer_w][0] != "hidden" else 16
        self._update_chrome()
        self.panes.apply(plan, self.centralWidget().width(), left, animate)

    def _update_chrome(self) -> None:
        """Buttons and bar contents that depend on the layout mode."""
        compact = self._mode == "compact"
        self.btn_menu.setVisible(self._mode != "wide")
        self.btn_menu.setToolTip(("Hide" if self._drawer_open else "Show") + " sidebar (Ctrl+B)")
        self.btn_screen.setVisible(compact)
        on_player = self._screen == "player"
        theme.bind(self.btn_screen, "library_music" if on_player else "music_note")
        self.btn_screen.setToolTip("Show your library" if on_player else "Show what's playing")
        self.bar_left.setFixedWidth(96 if compact else 230)
        self.bar_right.setFixedWidth(96 if compact else 230)
        self.vol.setVisible(not compact)
        self.btn_output.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                           else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        centre = compact and on_player
        align = Qt.AlignmentFlag.AlignHCenter if centre else Qt.AlignmentFlag.AlignLeft
        for lbl in (self.title_lbl, self.artist_lbl, self.album_lbl, self.plays_lbl):
            lbl.setAlignment(align)
        lay = self.cover.parentWidget().layout()
        for w in (self.cover, self.stars):
            lay.setAlignment(w, Qt.AlignmentFlag.AlignHCenter if centre else Qt.AlignmentFlag.AlignLeft)

    def _after_layout(self) -> None:
        self._fit_cover()
        self._fit_columns()

    def toggle_drawer(self) -> None:
        if self._mode == "wide":
            return
        self._drawer_open = not self._drawer_open
        if self._drawer_open and self._mode == "compact":
            self._screen = "library"
        self._apply_layout()

    def show_screen(self, screen: str) -> None:
        """Compact layout: switch between the player and the library."""
        if self._mode != "compact" or screen == self._screen:
            return
        self._screen = screen
        if screen == "player":
            self._drawer_open = False
        self._apply_layout()

    def _after_navigation(self) -> None:
        changed = False
        if self._mode in ("medium", "compact") and self._drawer_open:
            self._drawer_open = False
            changed = True
        if self._mode == "compact" and self._screen != "library":
            self._screen = "library"
            changed = True
        if changed and self._mode is not None:
            self._apply_layout()

    def _fit_cover(self) -> None:
        """The big cover grows with the player pane (it fills the window in compact mode)."""
        page = self.cover.parentWidget()
        free_h = page.height() - 190          # room left for title, artist, stars below
        side = int(max(160, min(self.np_w.width() - 48, free_h, 520)))
        if side != self._cover_side:
            self._cover_side = side
            self.cover.setFixedSize(side, side)
            self._set_cover(self._cover_src)

    def _fit_columns(self) -> None:
        w = self.table.viewport().width()
        self.table.setColumnHidden(3, w < 620)            # Album
        self.table.setColumnHidden(COL_RATING, w < 480)
        self.table.setColumnHidden(2, w < 340)            # Artist

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.cover and ev.type() == QEvent.Type.MouseButtonDblClick:
            self.show_fullscreen()
            return True
        if ev.type() == QEvent.Type.Resize:
            if obj is self.np_w and not self.panes.running:
                self._cover_timer.start()
            elif obj is self.table:
                self._fit_columns()
        elif ev.type() == QEvent.Type.Wheel and obj is self.btn_vol:
            step = 5 if ev.angleDelta().y() > 0 else -5     # scroll on the speaker icon = volume
            self.vol.setValue(max(0, min(self.vol.maximum(), self.vol.value() + step)))
            return True
        return super().eventFilter(obj, ev)
