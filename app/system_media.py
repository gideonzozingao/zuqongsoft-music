"""OS media integration.

Windows: the system media overlay (System Media Transport Controls: the flyout
next to the volume OSD, lock screen and Win+A quick settings) plus global hotkeys
registered with RegisterHotKey, which work while the app is in the background.

Other platforms get a no-op object, so the UI code never needs platform checks.

Commands are emitted as strings on `command`:
    play, pause, toggle, next, previous, stop, vol_up, vol_down
"""
from __future__ import annotations

import asyncio
import queue
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal


class MediaIntegration(QObject):
    command = Signal(str)
    message = Signal(str)

    hotkeys_supported = False
    hotkey_labels: list[str] = []

    def update_track(self, track: dict, cover: bytes | None) -> None: ...
    def update_state(self, state: str) -> None: ...
    def set_hotkeys_enabled(self, on: bool) -> None: ...
    def shutdown(self) -> None: ...


def create(window) -> MediaIntegration:
    if sys.platform == "win32":
        try:
            return WindowsMediaIntegration(window)
        except Exception as exc:          # never let OS glue stop the app from starting
            fallback = MediaIntegration(window)
            QTimer.singleShot(0, lambda: fallback.message.emit(f"Media integration disabled: {exc}"))
            return fallback
    return MediaIntegration(window)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    from PySide6.QtCore import QAbstractNativeEventFilter
    from PySide6.QtWidgets import QApplication

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL

    WM_HOTKEY = 0x0312
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000

    # (modifiers, virtual key, command, label)
    GLOBAL_HOTKEYS = [
        (MOD_CONTROL | MOD_ALT, 0x20, "toggle", "Ctrl+Alt+Space"),
        (MOD_CONTROL | MOD_ALT, 0x27, "next", "Ctrl+Alt+Right"),
        (MOD_CONTROL | MOD_ALT, 0x25, "previous", "Ctrl+Alt+Left"),
        (MOD_CONTROL | MOD_ALT, 0x26, "vol_up", "Ctrl+Alt+Up"),
        (MOD_CONTROL | MOD_ALT, 0x28, "vol_down", "Ctrl+Alt+Down"),
    ]
    # Only registered if the SMTC overlay is unavailable (SMTC already receives media keys).
    MEDIA_KEYS = [
        (0, 0xB3, "toggle", "Play/Pause key"),
        (0, 0xB0, "next", "Next key"),
        (0, 0xB1, "previous", "Previous key"),
        (0, 0xB2, "stop", "Stop key"),
    ]
    HOTKEY_BASE, MEDIA_BASE = 0x100, 0x200

    class _HotkeyFilter(QAbstractNativeEventFilter):
        def __init__(self, on_hotkey) -> None:
            super().__init__()
            self._on_hotkey = on_hotkey

        def nativeEventFilter(self, event_type, message):
            try:
                kind = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
                if bytes(kind) == b"windows_generic_MSG":
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY:
                        self._on_hotkey(int(msg.wParam))
                        return True, 0
            except Exception:
                pass
            return False, 0

    class _SmtcWorker(threading.Thread):
        """Owns every WinRT object on its own (MTA) thread so it never touches Qt's
        STA main thread. The UI posts ("track" | "state" | "quit", payload) tuples."""

        def __init__(self, on_button, on_ready) -> None:
            super().__init__(name="smtc", daemon=True)
            self._on_button = on_button
            self._on_ready = on_ready
            self.q: queue.Queue = queue.Queue()

        def post(self, kind: str, payload=None) -> None:
            if self.is_alive():
                self.q.put((kind, payload))

        def run(self) -> None:
            try:
                from winrt.windows.media import MediaPlaybackStatus, MediaPlaybackType
                from winrt.windows.media.playback import MediaPlayer
                from winrt.windows.storage.streams import (DataWriter, InMemoryRandomAccessStream,
                                                           RandomAccessStreamReference)
                # An idle MediaPlayer with its command manager off gives an unpackaged
                # desktop app its own SMTC session without playing anything through it.
                player = MediaPlayer()
                player.command_manager.is_enabled = False
                smtc = player.system_media_transport_controls
                for attr in ("is_play_enabled", "is_pause_enabled", "is_next_enabled",
                             "is_previous_enabled", "is_stop_enabled"):
                    setattr(smtc, attr, True)
                smtc.is_enabled = True

                def pressed(_sender, args) -> None:
                    name = getattr(args.button, "name", str(args.button)).lower()
                    if name in ("play", "pause", "next", "previous", "stop"):
                        self._on_button(name)

                token = smtc.add_button_pressed(pressed)
            except Exception as exc:
                self._on_ready(False, f"{type(exc).__name__}: {exc}")
                return
            self._on_ready(True, "")

            status = {"playing": MediaPlaybackStatus.PLAYING, "paused": MediaPlaybackStatus.PAUSED,
                      "stopped": MediaPlaybackStatus.STOPPED}

            async def thumbnail(data: bytes):
                stream = InMemoryRandomAccessStream()
                writer = DataWriter(stream)
                writer.write_bytes(data)
                await writer.store_async()
                await writer.flush_async()
                writer.detach_stream()
                stream.seek(0)
                return RandomAccessStreamReference.create_from_stream(stream)

            while True:
                jobs = {}
                kind, payload = self.q.get()
                jobs[kind] = payload
                while True:                       # coalesce bursts (e.g. skipping fast)
                    try:
                        kind, payload = self.q.get_nowait()
                    except queue.Empty:
                        break
                    jobs[kind] = payload
                if "quit" in jobs:
                    break
                if "track" in jobs:
                    title, artist, album, cover = jobs["track"]
                    try:
                        du = smtc.display_updater
                        du.type = MediaPlaybackType.MUSIC
                        du.music_properties.title = title
                        du.music_properties.artist = artist
                        du.music_properties.album_title = album
                        try:
                            du.thumbnail = asyncio.run(thumbnail(cover)) if cover else None
                        except Exception:
                            pass
                        du.update()
                    except Exception:
                        pass
                if "state" in jobs:
                    try:
                        smtc.playback_status = status[jobs["state"]]
                    except Exception:
                        pass
            try:
                smtc.remove_button_pressed(token)
                smtc.display_updater.clear_all()
                smtc.is_enabled = False
            except Exception:
                pass

    class WindowsMediaIntegration(MediaIntegration):
        hotkeys_supported = True
        hotkey_labels = [label for *_, label in GLOBAL_HOTKEYS]

        _button = Signal(str)
        _ready = Signal(bool, str)

        def __init__(self, window) -> None:
            super().__init__(window)
            self._hwnd = int(window.winId())
            self._commands: dict[int, str] = {}
            self._hotkeys_on = False
            self._filter = _HotkeyFilter(self._on_hotkey)
            QApplication.instance().installNativeEventFilter(self._filter)
            # Emitted from WinRT threads; Qt queues them onto the UI thread.
            self._button.connect(self.command)
            self._ready.connect(self._on_ready)
            self._worker = _SmtcWorker(self._button.emit, self._ready.emit)
            self._worker.start()

        def _register(self, specs, base: int) -> list[str]:
            failed = []
            for i, (mods, vk, cmd, label) in enumerate(specs):
                if _user32.RegisterHotKey(self._hwnd, base + i, mods | MOD_NOREPEAT, vk):
                    self._commands[base + i] = cmd
                else:
                    failed.append(label)
            return failed

        def _unregister(self, base: int, count: int) -> None:
            for hid in range(base, base + count):
                if self._commands.pop(hid, None) is not None:
                    _user32.UnregisterHotKey(self._hwnd, hid)

        def _on_ready(self, ok: bool, error: str) -> None:
            if ok:
                return
            failed = self._register(MEDIA_KEYS, MEDIA_BASE)
            note = "Install the winrt packages from requirements.txt to enable it."
            if "No module named" not in error:
                note = error
            self.message.emit(f"System media overlay unavailable; using media keys directly. {note}"
                              + (f" (could not claim: {', '.join(failed)})" if failed else ""))

        def _on_hotkey(self, hid: int) -> None:
            cmd = self._commands.get(hid)
            if cmd:   # leave the native event filter before doing real work
                QTimer.singleShot(0, lambda: self.command.emit(cmd))

        def set_hotkeys_enabled(self, on: bool) -> None:
            if on == self._hotkeys_on:
                return
            self._hotkeys_on = on
            if on:
                failed = self._register(GLOBAL_HOTKEYS, HOTKEY_BASE)
                if failed:
                    self.message.emit("These global hotkeys are taken by another app: " + ", ".join(failed))
            else:
                self._unregister(HOTKEY_BASE, len(GLOBAL_HOTKEYS))

        def update_track(self, track: dict, cover: bytes | None) -> None:
            self._worker.post("track", (track["title"], track["artist"], track["album"], cover))

        def update_state(self, state: str) -> None:
            self._worker.post("state", state)

        def shutdown(self) -> None:
            self._unregister(HOTKEY_BASE, len(GLOBAL_HOTKEYS))
            self._unregister(MEDIA_BASE, len(MEDIA_KEYS))
            self._worker.post("quit")
            self._worker.join(1.0)
            QApplication.instance().removeNativeEventFilter(self._filter)
