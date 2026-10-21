"""Playback engines.

BaseEngine owns the queue (order, shuffle, repeat); the backends only play files.
  * MpvEngine  - libmpv: gapless playback, 10-band equalizer, ReplayGain.
  * QtEngine   - Qt Multimedia fallback, used when libmpv isn't available (no EQ).
create_engine() picks the best one. The UI only uses the public methods/signals.
"""
from __future__ import annotations

import locale
import os
import random
import sys
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaPlayer

from .equalizer import EqState, band_command, af_chain


class RepeatMode(Enum):
    OFF = 0
    ALL = 1
    ONE = 2


class BaseEngine(QObject):
    positionChanged = Signal(int)   # ms
    durationChanged = Signal(int)   # ms
    playingChanged = Signal(bool)
    stateChanged = Signal(str)      # "playing" | "paused" | "stopped"
    trackChanged = Signal(object)   # track dict
    errorOccurred = Signal(str)
    queueChanged = Signal()         # Up Next changed (any reason)

    PREV_RESTART_MS = 3000          # "previous" restarts the track after this
    name = "base"
    supports_eq = False
    fallback_reason = ""
    libmpv_missing = False

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: list[dict] = []
        self._order: list[int] = []   # play order (indices into _queue)
        self._user: list[bool] = []   # parallel to _order: added with Play next / Add to queue
        self._pos = -1                # position inside _order
        self._shuffle = False
        self._repeat = RepeatMode.OFF
        self._err_streak = 0
        self.trackChanged.connect(self.queueChanged)

    # ---- state ---------------------------------------------------------
    @property
    def current_track(self) -> dict | None:
        if 0 <= self._pos < len(self._order):
            return self._queue[self._order[self._pos]]
        return None

    def upcoming(self) -> dict | None:
        """The track that follows naturally (used for gapless preloading)."""
        if self._repeat == RepeatMode.ONE or self._pos + 1 >= len(self._order):
            return None
        return self._queue[self._order[self._pos + 1]]

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    # ---- queue ---------------------------------------------------------
    def set_queue(self, tracks: list[dict], start: int = 0, autoplay: bool = True,
                  position_ms: int = 0) -> None:
        """Replace the queue. With autoplay=False the track is loaded paused at position_ms
        (used to restore the last session)."""
        queued = [self._queue[self._order[i]] for i in self._queued_positions()]
        self._queue = list(tracks)
        if not self._queue:
            self.stop()
            return
        self._err_streak = 0
        self._build_order(max(0, min(start, len(self._queue) - 1)))
        if queued:          # songs you queued survive starting something new
            self._insert(queued, self._pos + 1)
        self._load_current(autoplay=autoplay, position_ms=position_ms)
        self.queueChanged.emit()

    def _build_order(self, current: int) -> None:
        order = list(range(len(self._queue)))
        if self._shuffle:
            order.remove(current)
            random.shuffle(order)
            order.insert(0, current)
            self._pos = 0
        else:
            self._pos = current
        self._order = order
        self._user = [False] * len(order)

    # ---- Up Next ---------------------------------------------------------
    def _queued_positions(self) -> list[int]:
        """Order positions of the user-queued block right after the current track."""
        out, i = [], self._pos + 1
        while 0 <= self._pos and i < len(self._order) and self._user[i]:
            out.append(i)
            i += 1
        return out

    def _insert(self, tracks: list[dict], at: int) -> None:
        base = len(self._queue)
        self._queue.extend(tracks)
        for k in range(len(tracks)):
            self._order.insert(at + k, base + k)
            self._user.insert(at + k, True)

    def upcoming_items(self) -> list[tuple[dict, bool]]:
        """(track, queued_by_user) for everything after the current track, in play order."""
        return [(self._queue[self._order[i]], self._user[i]) for i in range(self._pos + 1, len(self._order))]

    def enqueue(self, tracks: list[dict], play_next: bool = False) -> None:
        """Play next: straight after the current song. Add to queue: after other queued songs."""
        if not tracks:
            return
        if self.current_track is None:
            self.set_queue(list(tracks), 0)
            return
        at = self._pos + 1 if play_next else self._pos + 1 + len(self._queued_positions())
        self._insert(list(tracks), at)
        self._queue_mutated()

    def move_upcoming(self, src: int, dst: int) -> None:
        n = len(self._order) - self._pos - 1
        if not (0 <= src < n and 0 <= dst < n) or src == dst:
            return
        i, j = self._pos + 1 + src, self._pos + 1 + dst
        idx, user = self._order.pop(i), self._user.pop(i)
        self._order.insert(j, idx)
        self._user.insert(j, user)
        self._queue_mutated()

    def remove_upcoming(self, rows: list[int]) -> None:
        for r in sorted(set(rows), reverse=True):
            i = self._pos + 1 + r
            if self._pos < i < len(self._order):
                del self._order[i]
                del self._user[i]
        self._queue_mutated()

    def clear_queued(self) -> None:
        self.remove_upcoming([p - self._pos - 1 for p in self._queued_positions()])

    def jump_to_upcoming(self, row: int) -> None:
        i = self._pos + 1 + row
        if self._pos < i < len(self._order):
            self._pos = i
            self._load_current(autoplay=True)

    def _queue_mutated(self) -> None:
        self._queue_changed()
        self.queueChanged.emit()

    def next(self, auto: bool = False) -> None:
        if not self._order:
            return
        if self._pos + 1 < len(self._order):
            self._pos += 1
        elif self._repeat == RepeatMode.ALL or not auto:
            start = random.randrange(len(self._queue)) if self._shuffle else 0
            self._build_order(start)
        else:                          # end of queue, repeat off
            self._stop_at_end()
            return
        self._load_current(autoplay=True)

    def previous(self) -> None:
        if not self._order:
            return
        if self.position > self.PREV_RESTART_MS or self._pos <= 0:
            self.seek(0)
            return
        self._pos -= 1
        self._load_current(autoplay=True)

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def set_shuffle(self, on: bool) -> None:
        self._shuffle = on
        if self._order:
            queued = [self._order[i] for i in self._queued_positions()]
            self._build_order(self._order[self._pos])
            for k, qi in enumerate(queued):          # queued songs stay right after the current one
                j = self._order.index(qi)
                del self._order[j]
                del self._user[j]
                if j < self._pos:
                    self._pos -= 1
                self._order.insert(self._pos + 1 + k, qi)
                self._user.insert(self._pos + 1 + k, True)
        self._queue_mutated()

    def set_repeat(self, mode: RepeatMode) -> None:
        self._repeat = mode
        self._queue_mutated()

    def cycle_repeat(self) -> None:
        modes = list(RepeatMode)
        self.set_repeat(modes[(modes.index(self._repeat) + 1) % len(modes)])

    def _skip_after_error(self, message: str) -> None:
        track = self.current_track
        self.errorOccurred.emit(f"Cannot play '{track['title'] if track else 'track'}': {message}")
        self._err_streak += 1
        if self._err_streak < len(self._queue):
            QTimer.singleShot(0, lambda: self.next(auto=True))
        else:
            self.stop()

    # ---- backend hooks -----------------------------------------------------
    def _queue_changed(self) -> None: ...
    def _load_current(self, autoplay: bool, position_ms: int = 0) -> None: raise NotImplementedError
    def _stop_at_end(self) -> None: self.stop()
    def play(self) -> None: raise NotImplementedError
    def pause(self) -> None: raise NotImplementedError
    def stop(self) -> None: raise NotImplementedError
    def seek(self, ms: int) -> None: raise NotImplementedError
    @property
    def position(self) -> int: return 0
    @property
    def precise_position(self) -> int: return self.position
    @property
    def is_playing(self) -> bool: return False
    def set_volume(self, percent: int) -> None: ...
    def set_audio_device(self, device: QAudioDevice) -> None: ...
    def set_equalizer(self, state: EqState) -> None: ...
    def set_normalize(self, on: bool) -> None: ...
    def shutdown(self) -> None: self.stop()


# ---- Qt Multimedia ---------------------------------------------------------------
class QtEngine(BaseEngine):
    name = "Qt Multimedia"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._pending_seek: int | None = None   # applied once the media has loaded
        self._resume_ms: int | None = None      # restored track not started yet

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(lambda d: self.durationChanged.emit(int(d)))
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def position(self) -> int:
        return self._resume_ms if self._resume_ms is not None else int(self._player.position())

    def _load_current(self, autoplay: bool, position_ms: int = 0) -> None:
        track = self.current_track
        if track is None:
            return
        self._pending_seek = position_ms if position_ms > 0 else None
        self._resume_ms = None if autoplay else max(0, position_ms)
        self._player.setSource(QUrl.fromLocalFile(track["path"]))
        self.trackChanged.emit(track)
        if autoplay:
            self._player.play()
        else:
            self.positionChanged.emit(max(0, position_ms))
            self.stateChanged.emit("paused")

    def play(self) -> None:
        if self.current_track is None:
            return
        self._player.play()
        if self._resume_ms is not None:
            if self._resume_ms > 0:
                self._player.setPosition(self._resume_ms)
            self._resume_ms = None

    def pause(self) -> None:
        if self.current_track is not None:
            self._player.pause()

    def stop(self) -> None:
        self._resume_ms = None
        self._player.stop()

    def seek(self, ms: int) -> None:
        ms = int(ms)
        if self._resume_ms is not None:          # restored but not started: remember it
            self._resume_ms = ms
            self._pending_seek = ms
            self.positionChanged.emit(ms)
        self._player.setPosition(ms)

    def set_volume(self, percent: int) -> None:
        level = max(0, min(100, percent)) / 100.0
        self._audio.setVolume(level * level)   # squared ~ perceptual curve

    def set_audio_device(self, device: QAudioDevice) -> None:
        if not device.isNull() and self._audio.device() != device:
            self._audio.setDevice(device)

    def _on_position(self, p: int) -> None:
        if self._resume_ms is None:
            self.positionChanged.emit(int(p))

    def _on_state(self, s: QMediaPlayer.PlaybackState) -> None:
        P = QMediaPlayer.PlaybackState
        self.playingChanged.emit(s == P.PlayingState)
        if s == P.PlayingState:
            self.stateChanged.emit("playing")
        elif s == P.PausedState or self.current_track is not None:
            self.stateChanged.emit("paused")
        else:
            self.stateChanged.emit("stopped")

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        S = QMediaPlayer.MediaStatus
        if status == S.EndOfMedia:
            if self._repeat == RepeatMode.ONE:
                self._player.setPosition(0)
                self._player.play()
            else:
                self.next(auto=True)
        elif status in (S.LoadedMedia, S.BufferedMedia):
            self._err_streak = 0
            if self._pending_seek is not None:
                self._player.setPosition(self._pending_seek)
                self._pending_seek = None

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self._skip_after_error(message or error.name)


# Backwards-compatible name.
PlayerEngine = QtEngine


# ---- mpv ---------------------------------------------------------------------------
END_EOF, END_ERROR = 0, 4


def _end_reason(event) -> tuple[int, str]:
    data = getattr(event, "data", None)
    if data is not None and hasattr(data, "reason"):          # python-mpv >= 1.0
        return int(data.reason), str(getattr(data, "error", "") or "")
    try:                                                       # older dict-style events
        e = event["event"]
        reason = e.get("reason", -1)
        if isinstance(reason, str):
            reason = {"eof": END_EOF, "error": END_ERROR}.get(reason, -1)
        return int(reason), str(e.get("error", "") or "")
    except Exception:
        return -1, ""


class MpvEngine(BaseEngine):
    """Gapless: the next track is always queued inside mpv, which switches to it
    without a gap; we notice via `playlist-pos` and preload the one after."""
    name = "mpv"
    supports_eq = True

    # mpv calls back on its own thread; these hop onto the UI thread.
    _sig_pos = Signal(int)
    _sig_dur = Signal(int)
    _sig_flags = Signal()
    _sig_plpos = Signal(int)
    _sig_end = Signal(int, str)
    _sig_loaded = Signal()

    def __init__(self, mpv_module, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._m = mpv_module.MPV(vid="no", vo="null", audio_display="no", idle="yes",
                                 gapless_audio="weak", audio_client_name="Music",
                                 input_default_bindings=False, input_vo_keyboard=False)
        # sub-auto/sid: mpv would otherwise load a song's .lrc file as a subtitle track.
        for opt, value in (("media-controls", "no"), ("input-media-keys", "no"),
                           ("sub-auto", "no"), ("sid", "no"),
                           ("replaygain-clip", "no"), ("volume-max", "100")):
            try:                                   # not every libmpv build has every option
                self._m[opt] = value
            except Exception:
                pass
        self._preloaded: dict | None = None
        self._pending_seek: int | None = None
        self._pos_ms = 0
        self._last_emit = -10_000
        self._paused = True
        self._idle = True
        self._eq_sent: EqState | None = None       # what the live filter chain currently has

        q = Qt.ConnectionType.QueuedConnection
        self._sig_pos.connect(self._on_pos, q)
        self._sig_dur.connect(lambda ms: self.durationChanged.emit(ms), q)
        self._sig_flags.connect(self._on_flags, q)
        self._sig_plpos.connect(self._on_plpos, q)
        self._sig_end.connect(self._on_end, q)
        self._sig_loaded.connect(self._on_loaded, q)

        self._m.observe_property("time-pos", self._obs_pos)
        self._m.observe_property("duration", lambda _n, v: self._sig_dur.emit(int((v or 0) * 1000)))
        self._m.observe_property("pause", self._obs_flag)
        self._m.observe_property("idle-active", self._obs_flag)
        self._m.observe_property("playlist-pos", lambda _n, v: self._sig_plpos.emit(-1 if v is None else int(v)))
        self._m.event_callback("end-file")(lambda ev: self._sig_end.emit(*_end_reason(ev)))
        self._m.event_callback("file-loaded")(lambda _ev: self._sig_loaded.emit())

    # -- callbacks on the mpv thread (keep tiny) --
    def _obs_pos(self, _name, value) -> None:
        if value is None:
            return
        ms = int(value * 1000)
        if abs(ms - self._last_emit) >= 200:      # 5 updates/second is plenty for the UI
            self._last_emit = ms
            self._sig_pos.emit(ms)

    def _obs_flag(self, name, value) -> None:
        if name == "pause":
            self._paused = bool(value)
        else:
            self._idle = bool(value)
        self._sig_flags.emit()

    # -- UI thread --
    @property
    def is_playing(self) -> bool:
        return not self._paused and not self._idle

    @property
    def position(self) -> int:
        return self._pos_ms

    @property
    def precise_position(self) -> int:
        """Exact playback time (position is updated only 5 times a second)."""
        try:
            t = self._m.time_pos
            return int(t * 1000) if t is not None else self._pos_ms
        except Exception:
            return self._pos_ms

    def _on_pos(self, ms: int) -> None:
        if self._pending_seek is None:
            self._pos_ms = ms
            self.positionChanged.emit(ms)

    def _on_flags(self) -> None:
        playing = self.is_playing
        self.playingChanged.emit(playing)
        if playing:
            self.stateChanged.emit("playing")
        else:
            self.stateChanged.emit("paused" if self.current_track is not None else "stopped")

    def _cmd(self, *args) -> bool:
        try:
            self._m.command(*[str(a) for a in args])
            return True
        except Exception:
            return False

    def _preload(self) -> None:
        self._cmd("playlist-clear")                # drops everything except the current file
        up = self.upcoming()
        self._preloaded = up if up and self._cmd("loadfile", up["path"], "append") else None

    def _load_current(self, autoplay: bool, position_ms: int = 0) -> None:
        track = self.current_track
        if track is None:
            return
        self._pending_seek = position_ms if position_ms > 0 else None
        self._pos_ms = max(0, position_ms)
        self._last_emit = -10_000
        self._m.pause = not autoplay
        self._preloaded = None
        self._cmd("loadfile", track["path"], "replace")
        self._preload()
        self.trackChanged.emit(track)
        self.positionChanged.emit(self._pos_ms)
        if not autoplay:
            self.stateChanged.emit("paused")

    def _on_plpos(self, pos: int) -> None:
        if pos == 1 and self._preloaded is not None:   # mpv moved on to the preloaded track
            self._pos += 1
            self._preloaded = None
            self._pos_ms = 0
            self._cmd("playlist-remove", 0)
            self._preload()
            self.trackChanged.emit(self.current_track)

    def _on_loaded(self) -> None:
        self._err_streak = 0
        if self._pending_seek is not None:
            self._cmd("seek", f"{self._pending_seek / 1000:.3f}", "absolute+exact")
            self._pending_seek = None

    def _on_end(self, reason: int, error: str) -> None:
        if reason == END_ERROR:
            track = self.current_track
            self.errorOccurred.emit(f"Cannot play '{track['title'] if track else 'track'}'"
                                    + (f" (mpv error {error})" if error else ""))
            self._err_streak += 1
            if self._preloaded is None:
                if self._err_streak < len(self._queue):
                    QTimer.singleShot(0, lambda: self.next(auto=True))
                else:
                    self.stop()
        elif reason == END_EOF and self._preloaded is None:
            QTimer.singleShot(0, lambda: self.next(auto=True))   # wrap-around or end of queue

    def _queue_changed(self) -> None:
        try:
            self._m["loop-file"] = "inf" if self._repeat == RepeatMode.ONE else "no"
        except Exception:
            pass
        if self.current_track is not None and not self._idle:
            self._preload()

    def _stop_at_end(self) -> None:
        self._cmd("stop")
        self._pos_ms = 0
        self.positionChanged.emit(0)

    def play(self) -> None:
        if self.current_track is None:
            return
        if self._idle:                                 # stopped at the end of the queue
            self._load_current(autoplay=True, position_ms=self._pos_ms)
        else:
            self._m.pause = False

    def pause(self) -> None:
        if self.current_track is not None:
            self._m.pause = True

    def stop(self) -> None:
        self._cmd("stop")

    def seek(self, ms: int) -> None:
        ms = max(0, int(ms))
        self._pos_ms = ms
        self._last_emit = ms
        if self._idle:                                 # nothing loaded right now: apply on play
            self.positionChanged.emit(ms)
            return
        if self._pending_seek is not None:             # still loading
            self._pending_seek = ms
        else:
            self._cmd("seek", f"{ms / 1000:.3f}", "absolute+exact")
        self.positionChanged.emit(ms)

    def set_volume(self, percent: int) -> None:
        self._m.volume = max(0, min(100, percent))    # mpv's volume is already perceptual

    def set_audio_device(self, device: QAudioDevice) -> None:
        if device.isNull():
            return
        dev_id = bytes(device.id().data()).decode("utf-8", "replace")
        name = "auto"
        try:
            for d in self._m.audio_device_list:        # e.g. "wasapi/{0.0.0.00000000}.{guid}"
                if d["name"] == dev_id or d["name"].endswith("/" + dev_id):
                    name = d["name"]
                    break
            if self._m.audio_device != name:
                self._m.audio_device = name
        except Exception:
            pass

    def set_equalizer(self, state: EqState) -> None:
        prev = self._eq_sent
        if prev is not None and prev.enabled and state.enabled:
            # Live update: only send changed bands (no re-init, no glitch).
            ok = True
            if prev.preamp != state.preamp:
                ok &= self._cmd("af-command", "eqpre", "volume", f"{state.preamp:.1f}dB", "Parsed_volume_0")
            for i, (a, b) in enumerate(zip(prev.gains, state.gains)):
                if a != b:
                    ok &= self._cmd(*band_command(i, b))
            if ok:
                self._eq_sent = state.copy()
                return
        chain = af_chain(state)
        self._cmd("af", "set", chain) if chain else self._cmd("af", "clr", "")
        self._eq_sent = state.copy()

    def set_normalize(self, on: bool) -> None:
        try:
            self._m["replaygain"] = "track" if on else "no"
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self._m.terminate()
        except Exception:
            pass


# ---- factory -------------------------------------------------------------------
def _prepare_libmpv() -> None:
    """Make libmpv loadable, or raise with a message that says exactly what's wrong."""
    from . import libmpv
    if sys.platform == "win32":
        import struct
        dll = libmpv.find()
        if dll is None:
            raise FileNotFoundError("libmpv-2.dll was not found. Looked in: "
                                    + "; ".join(str(d) for d in libmpv.search_dirs()))
        if struct.calcsize("P") == 4:
            raise OSError("this is 32-bit Python, but libmpv-2.dll is 64-bit. Install 64-bit Python.")
        folder = str(dll.parent)
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(folder)
    else:   # Linux/macOS use the system libmpv; a project lib folder still wins if present
        for d in libmpv.search_dirs():
            if d.is_dir():
                os.environ["LD_LIBRARY_PATH"] = str(d) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")


def create_engine(parent: QObject | None = None) -> BaseEngine:
    """mpv when libmpv can be loaded, otherwise Qt Multimedia (without equalizer)."""
    missing = False
    if os.environ.get("MUSICPLAYER_ENGINE", "").lower() != "qt":
        try:
            _prepare_libmpv()
            locale.setlocale(locale.LC_NUMERIC, "C")    # libmpv refuses to start otherwise
            import mpv
            return MpvEngine(mpv, parent)
        except FileNotFoundError as exc:
            missing, reason = True, str(exc)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
    else:
        reason = "disabled with MUSICPLAYER_ENGINE=qt"
    engine = QtEngine(parent)
    engine.fallback_reason = reason
    engine.libmpv_missing = missing
    return engine
