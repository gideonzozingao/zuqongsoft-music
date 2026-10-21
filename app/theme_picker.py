"""Theme picker: a gallery dialog with live previews, and a quick menu for View > Theme."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QActionGroup, QColor, QIcon
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout)

from . import theme

MODE_LABELS = (("light", "Light"), ("dark", "Dark"), ("system", "System"))


def pick_custom_colour(parent, current: str | None) -> str | None:
    colour = QColorDialog.getColor(QColor(current or theme.C["primary"]), parent,
                                   "Pick a colour to build a theme from")
    return colour.name() if colour.isValid() else None


class ThemeDialog(QDialog):
    """Changes apply immediately; the dialog just stays open so you can compare."""

    COLUMNS = 4

    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self.setWindowTitle("Appearance")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Appearance", objectName="dialogTitle"))

        lay.addWidget(QLabel("Mode", objectName="muted"))
        self.modes = QButtonGroup(self)
        row = QHBoxLayout()
        row.setSpacing(0)
        for i, (key, label) in enumerate(MODE_LABELS):
            b = QPushButton(label, objectName="segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("seg", "first" if i == 0 else "last" if i == len(MODE_LABELS) - 1 else "mid")
            b.setProperty("mode", key)
            self.modes.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.mode_note = QLabel("", objectName="muted")
        lay.addWidget(self.mode_note)

        lay.addSpacing(4)
        lay.addWidget(QLabel("Colour", objectName="muted"))
        self.cards = QButtonGroup(self)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, name in enumerate([theme.ALBUM, *theme.PRESETS, theme.CUSTOM]):
            b = QToolButton(objectName="themeCard")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(132, 88))
            b.setText("Custom…" if name == theme.CUSTOM else name)
            b.setProperty("preset", name)
            self.cards.addButton(b)
            grid.addWidget(b, i // self.COLUMNS, i % self.COLUMNS)
        lay.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        lay.addWidget(buttons)

        self.modes.buttonClicked.connect(lambda b: self._choose(mode=b.property("mode")))
        self.cards.buttonClicked.connect(self._card_clicked)
        self.sync()

    def _card_clicked(self, b: QToolButton) -> None:
        name = b.property("preset")
        if name == theme.CUSTOM:
            _preset, _mode, seed = self.win.theme_state()
            colour = pick_custom_colour(self, seed)
            if colour is None:
                self.sync()
                return
            self._choose(preset=theme.CUSTOM, seed=colour)
        else:
            self._choose(preset=name)

    def _choose(self, **changes) -> None:
        self.win.set_theme(**changes)
        self.sync()

    def sync(self) -> None:
        """Reflect the current theme and redraw previews for the current light/dark mode."""
        preset, mode, seed = self.win.theme_state()
        forced = theme.PRESETS.get(preset, {}).get("force")
        for b in self.modes.buttons():
            b.setChecked(b.property("mode") == mode)
            b.setEnabled(not forced)
        self.mode_note.setText(f"{preset} is always {forced}." if forced else
                               "System follows your Windows light/dark setting." if mode == "system" else "")
        self.mode_note.setVisible(bool(self.mode_note.text()))
        for b in self.cards.buttons():
            name = b.property("preset")
            b.setChecked(name == preset)
            b.setIcon(QIcon(theme.preview(theme.scheme_for(name, theme.is_dark, seed))))
            if name == theme.ALBUM:
                b.setToolTip("Recolours the whole player from the playing song's cover art")
            if name == theme.CUSTOM:
                b.setToolTip(f"Build a theme from any colour (current: {seed})" if seed
                             else "Build a theme from any colour")


def fill_theme_menu(menu: QMenu, window) -> None:
    """Quick picker: presets with swatches, light/dark/system, and shortcuts to the rest."""
    menu.clear()
    preset, mode, seed = window.theme_state()
    group = QActionGroup(menu)
    for name in [theme.ALBUM, *theme.PRESETS, *([theme.CUSTOM] if seed else [])]:
        act = menu.addAction(theme.swatch(theme.scheme_for(name, theme.is_dark, seed)),
                             "Match album art" if name == theme.ALBUM else name)
        act.setCheckable(True)
        act.setChecked(name == preset)
        act.setActionGroup(group)
        act.triggered.connect(lambda _c=False, n=name: window.set_theme(preset=n))
    menu.addSeparator()
    modes = QActionGroup(menu)
    forced = theme.PRESETS.get(preset, {}).get("force")
    for key, label in MODE_LABELS:
        act = menu.addAction("Follow system" if key == "system" else label)
        act.setCheckable(True)
        act.setChecked(key == mode)
        act.setEnabled(not forced)
        act.setActionGroup(modes)
        act.triggered.connect(lambda _c=False, k=key: window.set_theme(mode=k))
    menu.addSeparator()
    custom = menu.addAction("Custom colour…")
    custom.triggered.connect(lambda: _menu_custom(window))
    more = menu.addAction("Appearance…\tCtrl+T")
    more.triggered.connect(window.show_theme_dialog)


def _menu_custom(window) -> None:
    _preset, _mode, seed = window.theme_state()
    colour = pick_custom_colour(window, seed)
    if colour:
        window.set_theme(preset=theme.CUSTOM, seed=colour)
