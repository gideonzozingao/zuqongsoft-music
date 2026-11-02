"""Audio output devices: choosing, Bluetooth, hot-plug handling."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QMenu
from ..audio_devices import KIND_ICONS, OutputDevice, open_bluetooth_settings, open_sound_settings
from ..theme import C, icon
from .widgets import add_action


class OutputMixin:
    """Part of MainWindow: audio output devices: choosing, Bluetooth, hot-plug handling."""

    # ---- equalizer -----------------------------------------------------------
    def current_output_name(self) -> str:
        return self._current_out.name if self._current_out else ""

    def _out_id(self) -> str | None:
        return self._current_out.id if self._current_out else None

    # ---- audio output devices ----------------------------------------------
    def _apply_output(self) -> None:
        """Route audio to the chosen device, or the system default if it isn't connected."""
        target = self.devices.get(self._pinned_id) or self.devices.default()
        if target is not None:
            self.engine.set_audio_device(target.qdevice)
        self._current_out = target
        self._update_output_chip()
        self._load_eq_for_device()

    def _select_output(self, dev_id: str) -> None:
        dev = self.devices.get(dev_id)
        self._pinned_id = dev_id
        self._pinned_name = dev.name if dev else ""
        self.settings.setValue("audio/device", dev_id)
        self.settings.setValue("audio/device_name", self._pinned_name)
        self._apply_output()

    def _update_output_chip(self) -> None:
        d = self._current_out
        name = d.name if d else "No audio output"
        bt = bool(d and d.kind == "bluetooth")
        self.btn_output.setIcon(icon(KIND_ICONS[d.kind] if d else "volume_off",
                                     C["primary"] if bt else C["on_surface_variant"]))
        chip_font = QFont(self.btn_output.font())          # the chip's style sheet font: 13 px, semibold
        chip_font.setPixelSize(13)
        chip_font.setWeight(QFont.Weight.DemiBold)
        self.btn_output.setText(QFontMetrics(chip_font).elidedText(name, Qt.TextElideMode.ElideRight, 170))
        self.btn_output.setProperty("bt", bt)
        self.btn_output.style().unpolish(self.btn_output)
        self.btn_output.style().polish(self.btn_output)
        tip = [f"Playing on {name}", "Click to choose another output"]
        if self._pinned_id and self.devices.get(self._pinned_id) is None:
            tip.insert(1, f"{self._pinned_name or 'Your chosen device'} is not connected. "
                          "Playback returns to it when it reconnects.")
        if d and d.handsfree:
            tip.insert(1, "This is the hands-free (call) output: mono and low quality. "
                          "Pick the stereo entry for the same device.")
        self.btn_output.setToolTip("\n".join(tip))

    def _fill_output_menu(self, menu: QMenu) -> None:
        menu.clear()
        menu.setToolTipsVisible(True)
        default = self.devices.default()
        label = "System default" + (f" ({default.name})" if default else "")
        act = menu.addAction(icon("check", C["primary"]) if not self._pinned_id else icon("speaker"), label)
        act.triggered.connect(lambda _c=False: self._select_output(""))

        devices = self.devices.devices()
        if devices or self._pinned_id:
            menu.addSeparator()
        for d in devices:
            text = d.name + ("  (hands-free, low quality)" if d.handsfree else "")
            chosen = d.id == self._pinned_id
            act = menu.addAction(icon("check", C["primary"]) if chosen else icon(KIND_ICONS[d.kind]), text)
            if d.kind == "bluetooth":
                act.setToolTip("Bluetooth" + (": call profile, use the stereo entry for music" if d.handsfree else ""))
            act.triggered.connect(lambda _c=False, i=d.id: self._select_output(i))
        if self._pinned_id and self.devices.get(self._pinned_id) is None:
            act = menu.addAction(icon("check", C["primary"]),
                                 f"{self._pinned_name or 'Chosen device'} (not connected)")
            act.setEnabled(False)

        menu.addSeparator()
        add_action(menu, "bluetooth", "Connect a Bluetooth device…", self._open_bluetooth)
        add_action(menu, "settings", "Sound settings…", self._open_sound)
        menu.addSeparator()
        for text, attr in (("Switch to headphones and Bluetooth when they connect", "_auto_switch"),
                           ("Pause when headphones or Bluetooth disconnect", "_pause_on_disconnect")):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(getattr(self, attr))
            act.toggled.connect(lambda on, a=attr: setattr(self, a, on))

    def _on_device_disconnected(self, d: OutputDevice) -> None:
        if self._current_out is None or d.id != self._current_out.id:
            return
        paused = self._pause_on_disconnect and d.personal and self.engine.is_playing
        if paused:
            self.engine.pause()
        self.statusBar().showMessage(f"{d.name} disconnected" + (", playback paused" if paused else ""), 6000)

    def _on_device_connected(self, d: OutputDevice) -> None:
        if d.id == self._pinned_id:
            self.statusBar().showMessage(f"Reconnected to {d.name}", 5000)
        elif self._auto_switch and d.personal and not d.handsfree:
            self._select_output(d.id)
            self.statusBar().showMessage(f"Now playing on {d.name}", 5000)

    def _open_bluetooth(self) -> None:
        if open_bluetooth_settings():
            self.statusBar().showMessage(
                "Pair and connect your device in Bluetooth settings; it will appear here automatically.", 8000)
        else:
            self.statusBar().showMessage("Could not open Bluetooth settings on this system.", 6000)

    def _open_sound(self) -> None:
        if not open_sound_settings():
            self.statusBar().showMessage("Could not open sound settings on this system.", 6000)
