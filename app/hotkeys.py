"""Global hotkeys on Windows via RegisterHotKey (ctypes only, no extra dependencies).

Edit DEFAULT_HOTKEYS below to change the key combinations. Format: "Ctrl+Alt+Space",
modifiers Ctrl/Alt/Shift/Win, keys A-Z, 0-9, F1-F12, Space, arrows, Home/End/PageUp/PageDown.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

# (action, key combination)
DEFAULT_HOTKEYS: list[tuple[str, str]] = [
    ("play_pause", "Ctrl+Alt+Space"),
    ("next", "Ctrl+Alt+Right"),
    ("previous", "Ctrl+Alt+Left"),
    ("volume_up", "Ctrl+Alt+Up"),
    ("volume_down", "Ctrl+Alt+Down"),
]
# Hardware media keys. Only registered when the Windows media overlay (SMTC) is NOT
# available; with SMTC, Windows already routes the media keys to us without grabbing them
# away from other players.
MEDIA_KEY_HOTKEYS: list[tuple[str, str]] = [
    ("play_pause", "MediaPlayPause"),
    ("next", "MediaNext"),
    ("previous", "MediaPrevious"),
    ("stop", "MediaStop"),
]

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
         "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN}
_KEYS = {"space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
         "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
         "insert": 0x2D, "delete": 0x2E,
         "mediaplaypause": 0xB3, "medianext": 0xB0, "mediaprevious": 0xB1, "mediastop": 0xB2}
_KEYS.update({f"f{i}": 0x6F + i for i in range(1, 13)})


def parse_hotkey(text: str) -> tuple[int, int]:
    """'Ctrl+Alt+Space' -> (modifier flags, virtual-key code)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    flags = 0
    for m in mods:
        if m not in _MODS:
            raise ValueError(f"unknown modifier '{m}'")
        flags |= _MODS[m]
    if key in _KEYS:
        return flags, _KEYS[key]
    if len(key) == 1 and key.isalnum():
        return flags, ord(key.upper())
    raise ValueError(f"unknown key '{key}'")


def _default_user32():
    if sys.platform != "win32":
        return None
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.RegisterHotKey.restype = wintypes.BOOL
    u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u.UnregisterHotKey.restype = wintypes.BOOL
    return u


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_msg) -> None:
        super().__init__()
        self._on_msg = on_msg

    def nativeEventFilter(self, eventType, message):
        # WM_HOTKEY is posted to the window we registered with -> "windows_generic_MSG"
        if bytes(eventType) == b"windows_generic_MSG":
            self._on_msg(int(message))
        return False, 0


class GlobalHotkeys(QObject):
    triggered = Signal(str)       # action name

    def __init__(self, parent=None, user32=None) -> None:
        super().__init__(parent)
        self._user32 = user32 if user32 is not None else _default_user32()
        self._filter = _Filter(self._on_msg)
        self._installed = False
        self._hwnd = 0
        self._ids: dict[int, str] = {}

    @property
    def supported(self) -> bool:
        return self._user32 is not None

    def register(self, hwnd: int, bindings: list[tuple[str, str]]) -> list[str]:
        """Register hotkeys; returns the combinations that could not be registered
        (typically because another application already owns them)."""
        self.unregister()
        failed: list[str] = []
        if self._user32 is None:
            return failed
        self._hwnd = hwnd
        for n, (action, text) in enumerate(bindings, start=1):
            try:
                mods, vk = parse_hotkey(text)
            except ValueError:
                failed.append(text)
                continue
            if self._user32.RegisterHotKey(hwnd, n, mods | MOD_NOREPEAT, vk):
                self._ids[n] = action
            else:
                failed.append(text)
        if self._ids and not self._installed:
            QCoreApplication.instance().installNativeEventFilter(self._filter)
            self._installed = True
        return failed

    def unregister(self) -> None:
        if self._user32 is not None:
            for n in self._ids:
                self._user32.UnregisterHotKey(self._hwnd, n)
        self._ids.clear()
        if self._installed:
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._installed = False

    def _on_msg(self, address: int) -> None:
        msg = wintypes.MSG.from_address(address)
        if msg.message == WM_HOTKEY:
            action = self._ids.get(int(msg.wParam))
            if action:
                self.triggered.emit(action)
