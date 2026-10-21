"""Audio output devices: discovery, hot-plug events and Bluetooth detection.

Bluetooth speakers and headphones are paired and connected by the operating
system. Once connected they show up here like any other output, and this module
reports them as they appear and disappear, so the player can follow them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioDevice, QMediaDevices

KIND_ORDER = {"bluetooth": 0, "headphones": 1, "usb": 2, "hdmi": 3, "speaker": 4}
KIND_ICONS = {"bluetooth": "bluetooth", "headphones": "headphones", "usb": "usb",
              "hdmi": "tv", "speaker": "speaker"}


@dataclass(frozen=True, eq=False)
class OutputDevice:
    id: str
    name: str
    kind: str            # bluetooth | headphones | usb | hdmi | speaker
    handsfree: bool      # Bluetooth call profile: mono, low quality
    qdevice: QAudioDevice

    @property
    def personal(self) -> bool:
        """Headphones or Bluetooth: devices that come and go with the listener."""
        return self.kind in ("bluetooth", "headphones")


# ---- classification ----------------------------------------------------------
_BT_WORDS = ("bluetooth", "airpods", "buds", "hands-free", "handsfree", "a2dp")
_HF_WORDS = ("hands-free", "handsfree", "ag audio")
_HEADPHONE_WORDS = ("headphone", "headset", "earphone", "earbud")
_HDMI_WORDS = ("hdmi", "displayport", "display audio", "nvidia high definition",
               "amd high definition", "intel(r) display", " tv")
_MAC_BT_UID = re.compile(r"^([0-9a-f]{2}[-:]){5}[0-9a-f]{2}")


@lru_cache(maxsize=256)
def _windows_bluetooth(endpoint_id: str) -> tuple[bool | None, bool]:
    """Look the WASAPI endpoint up in the registry; Bluetooth endpoints carry a
    BTHENUM (stereo) or BTHHFENUM (hands-free) device path in their properties."""
    m = re.search(r"\{[0-9A-Fa-f-]{36}\}$", endpoint_id)
    if not m:
        return None, False
    import winreg
    path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{m.group(0)}\Properties"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None, False
    bt = hf = False
    with key:
        i = 0
        while True:
            try:
                _name, value, _type = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if isinstance(value, str):
                v = value.lower()
                if "bthhfenum" in v:
                    bt = hf = True
                elif "bthenum" in v or "bthledevice" in v:
                    bt = True
    return bt, hf


def _platform_bluetooth(dev_id: str) -> tuple[bool | None, bool]:
    """(is_bluetooth or None if unknown, is_handsfree) from the platform's device id."""
    low = dev_id.lower()
    if sys.platform == "win32":
        try:
            return _windows_bluetooth(dev_id)
        except Exception:
            return None, False
    if sys.platform == "darwin":          # CoreAudio UIDs of BT devices start with the MAC address
        return (True if _MAC_BT_UID.match(low) else None), False
    if "bluez" in low:                     # PulseAudio / PipeWire sink names
        return True, any(p in low for p in ("head_unit", "headset", "handsfree", "hsp", "hfp"))
    return None, False


def classify(dev_id: str, name: str) -> tuple[str, bool]:
    low = f" {name.lower()} "
    bt, hf = _platform_bluetooth(dev_id)
    if bt is None:
        bt = any(w in low for w in _BT_WORDS)
    hf = hf or (bt and any(w in low for w in _HF_WORDS))
    if bt:
        return "bluetooth", hf
    if any(w in low for w in _HEADPHONE_WORDS):
        return "headphones", False
    if any(w in low for w in _HDMI_WORDS):
        return "hdmi", False
    if "usb" in low:
        return "usb", False
    return "speaker", False


def _device_id(dev: QAudioDevice) -> str:
    return bytes(dev.id().data()).decode("utf-8", "replace")


# ---- manager -------------------------------------------------------------------
class AudioDeviceManager(QObject):
    devicesChanged = Signal()
    deviceConnected = Signal(object)      # OutputDevice
    deviceDisconnected = Signal(object)   # OutputDevice (emitted before devicesChanged)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._media = QMediaDevices(self)
        self._devices = self._scan()
        # Connecting a Bluetooth headset adds several endpoints in a burst; wait for it to settle.
        self._debounce = QTimer(self, singleShot=True, interval=600)
        self._debounce.timeout.connect(self.refresh)
        self._media.audioOutputsChanged.connect(self._debounce.start)

    @staticmethod
    def _wrap(dev: QAudioDevice) -> OutputDevice:
        dev_id = _device_id(dev)
        kind, hf = classify(dev_id, dev.description())
        return OutputDevice(dev_id, dev.description(), kind, hf, dev)

    def _scan(self) -> dict[str, OutputDevice]:
        out: dict[str, OutputDevice] = {}
        for dev in QMediaDevices.audioOutputs():
            d = self._wrap(dev)
            out.setdefault(d.id, d)
        return out

    def devices(self) -> list[OutputDevice]:
        return sorted(self._devices.values(),
                      key=lambda d: (KIND_ORDER[d.kind], d.handsfree, d.name.casefold()))

    def get(self, dev_id: str) -> OutputDevice | None:
        return self._devices.get(dev_id) if dev_id else None

    def default(self) -> OutputDevice | None:
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            return None
        return self._devices.get(_device_id(dev)) or self._wrap(dev)

    def refresh(self) -> None:
        old, new = self._devices, self._scan()
        self._devices = new
        for dev_id in old.keys() - new.keys():
            self.deviceDisconnected.emit(old[dev_id])
        for dev_id in new.keys() - old.keys():
            self.deviceConnected.emit(new[dev_id])
        self.devicesChanged.emit()     # also covers a changed system default


# ---- OS settings pages ------------------------------------------------------------
def _launch_first(candidates: list[list[str]]) -> bool:
    for cmd in candidates:
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


def _open_uri(uri: str) -> bool:
    try:
        if sys.platform == "win32":
            os.startfile(uri)  # noqa: S606 - ms-settings: URI
        else:
            subprocess.Popen(["open", uri])
        return True
    except OSError:
        return False


def open_bluetooth_settings() -> bool:
    if sys.platform == "win32":
        return _open_uri("ms-settings:bluetooth")
    if sys.platform == "darwin":
        return _open_uri("x-apple.systempreferences:com.apple.BluetoothSettings")
    return _launch_first([["gnome-control-center", "bluetooth"], ["blueman-manager"],
                          ["systemsettings", "kcm_bluetooth"], ["systemsettings5", "bluetooth"]])


def open_sound_settings() -> bool:
    if sys.platform == "win32":
        return _open_uri("ms-settings:sound")
    if sys.platform == "darwin":
        return _open_uri("x-apple.systempreferences:com.apple.preference.sound")
    return _launch_first([["gnome-control-center", "sound"], ["pavucontrol"],
                          ["systemsettings", "kcm_pulseaudio"]])
