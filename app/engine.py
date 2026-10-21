"""Playback engines.

BaseEngine owns the queue (order, shuffle, repeat); the backends only play files.
  * MpvEngine  - libmpv: gapless playback, 10-band equalizer, ReplayGain.
  * QtEngine   - Qt Multimedia fallback, used when libmpv isn't available (no EQ).
create_engine() picks the best one. The UI only uses the public methods/signals.
"""
from __future__ import annotations

import locale
import math
import os
import time
import random
import sys
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaPlayer

from .equalizer import ChainOptions, EqState, band_command, build_chain
from .studio import live_commands


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
    max_volume = 100              # mpv: 200 (boost); Qt Multimedia: 100
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

    def extend(self, tracks: list[dict]) -> None:
        """Append to the end of the queue (Song Radio / autoplay)."""
        if not tracks:
            return
        if self.current_track is None:
            self.set_queue(list(tracks), 0)
            return
        base = len(self._queue)
        self._queue.extend(tracks)
        self._order.extend(range(base, base + len(tracks)))
        self._user.extend([False] * len(tracks))
        self._queue_mutated()

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
    def set_loudness(self, on: bool) -> None: ...
    def set_sound_options(self, exclusive: bool, hq_resample: bool, channels: str) -> None: ...
    def audio_info(self) -> dict | None: return None
    def set_bypass(self, gain_db: float | None) -> None: ...
    def set_meter(self, on: bool) -> None: ...
    def meter(self) -> dict | None: return None
    def set_solo(self, band: int | None) -> None: ...
    def set_rta(self, on: bool) -> None: ...
    def rta(self) -> list[float] | None: return None
    def levels(self) -> dict | None: return None
    def set_track_gain(self, db: float | None) -> None: ...
    def set_speaker(self, speaker) -> None: ...
    def set_dj(self, crossfade_ms: int, trim_silence: bool, fade_controls: bool, keep_albums: bool) -> None: ...
    def set_karaoke(self, vocal: float, semitones: int) -> None: ...
    def fade_to(self, factor: float, ms: int, then=None) -> None:
        if then:
            then()
    trim_lookup = None              # callable(path) -> (intro_ms, outro_ms) or None
    def set_solo(self, band: int | None) -> None:
        if band != self._solo:
            self._solo = band
            self._rebuild()

    def set_rta(self, on: bool) -> None:
        if on != self._rta:
            self._rta = on
            self._rebuild()

    def set_speaker(self, speaker) -> None:
        if speaker != self._speaker:
            self._speaker = speaker
            self._rebuild()

    def set_track_gain(self, db: float | None) -> None:
        """Library loudness levelling: gain for the song that's playing (None = off)."""
        if db is None or self._level_sent is None:
            if db != self._level:
                self._level = db
                self._rebuild()
            return
        self._level = db
        if abs(db - self._level_sent) > 0.01:
            if self._cmd("af-command", "lvl", "volume", f"{db:.2f}dB", "Parsed_volume_0"):
                self._level_sent = db
            else:
                self._rebuild()

    def _metadata(self, label: str) -> dict:
        try:
            return self._m._get_property(f"af-metadata/{label}") or {}
        except Exception:
            return {}

    def rta(self) -> list[float] | None:
        if not self._rta or self._idle:
            return None
        from .tools import RTA_FREQS
        out = []
        for i in range(len(RTA_FREQS)):
            v = self._metadata(f"rta{i}").get("lavfi.astats.3.RMS_level")
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(-120.0)
        return out

    def levels(self) -> dict | None:
        if not self._meter or self._idle:
            return None
        md = self._metadata("levels")
        if not md:
            return None
        def f(key):
            try:
                return float(md.get(key))
            except (TypeError, ValueError):
                return None
        return {"peak": [f("lavfi.astats.1.Peak_level"), f("lavfi.astats.2.Peak_level")],
                "rms": [f("lavfi.astats.1.RMS_level"), f("lavfi.astats.2.RMS_level")],
                "phase": f("lavfi.aphasemeter.phase")}

    def reset_meter(self) -> None: ...
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
    max_volume = 200

    # mpv calls back on its own thread; these hop onto the UI thread.
    _sig_pos = Signal(int)
    _sig_dur = Signal(int)
    _sig_flags = Signal()
    _sig_plpos = Signal(int)
    _sig_end = Signal(int, str)
    _sig_loaded = Signal()

    def __init__(self, mpv_module, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mpv = mpv_module
        self._m = self._make_player()
        self._spare = None                          # second deck for crossfades
        self._fading = None                         # deck currently fading out
        self._preloaded: dict | None = None
        self._pending_seek: int | None = None
        self._pos_ms = 0
        self._last_emit = -10_000
        self._paused = True
        self._idle = True
        self._eq_sent: EqState | None = None       # what the live filter chain currently has
        self._eq = EqState()
        self._boost_db = 0.0                        # gain above 100% volume, in dB
        self._boost_sent = 0.0
        self._loudness = False
        self._volume = 100
        self._replaygain = False
        self._bypass: float | None = None          # A/B: level-matching gain while comparing
        self._meter = False
        self._solo: int | None = None
        self._rta = False
        self._level: float | None = None           # library levelling gain for the current song
        self._level_sent: float | None = None
        self._speaker = None
        self._chain = ""                            # last filter chain (copied to a new deck)
        self._opts: dict[str, str] = {}             # mpv options to copy to a new deck
        self._device = "auto"
        self._karaoke = (0.0, 0)
        # DJ mode
        self._xf_ms = 0
        self._trim = False
        self._fade_controls = False
        self._keep_albums = True
        self._exclusive = False
        self._dur_ms = 0
        self._fade = 1.0                            # master fade factor (pause/play/sleep fades)
        self._fade_anim = None
        self._xf = None                             # running crossfade: {"old", "t0", "ms"}
        self._xf_timer = QTimer(self, interval=30)
        self._xf_timer.timeout.connect(self._xf_step)
        self._dj_timer = QTimer(self, interval=100)
        self._dj_timer.timeout.connect(self._dj_check)

        q = Qt.ConnectionType.QueuedConnection
        self._sig_pos.connect(self._on_pos, q)
        self._sig_dur.connect(lambda ms: self.durationChanged.emit(ms), q)
        self._sig_flags.connect(self._on_flags, q)
        self._sig_plpos.connect(self._on_plpos, q)
        self._sig_end.connect(self._on_end, q)
        self._sig_loaded.connect(self._on_loaded, q)

        self._sig_dur.connect(self._on_dur, q)
        self._wire(self._m)

    # -- players ("decks") --
    def _make_player(self):
        m = self._mpv.MPV(vid="no", vo="null", audio_display="no", idle="yes",
                          gapless_audio="weak", audio_client_name="Zuqongsoft Music",
                          input_default_bindings=False, input_vo_keyboard=False)
        # sub-auto/sid: mpv would otherwise load a song's .lrc file as a subtitle track.
        for opt, value in (("media-controls", "no"), ("input-media-keys", "no"),
                           ("sub-auto", "no"), ("sid", "no"),
                           ("replaygain-clip", "no"), ("volume-max", "100")):   # >100 is done by our boost filter
            try:                                   # not every libmpv build has every option
                m[opt] = value
            except Exception:
                pass
        return m

    def _wire(self, m) -> None:
        """Observers only report for the live deck; a deck that is fading out stays silent."""
        live = lambda: m is self._m  # noqa: E731
        m.observe_property("time-pos", lambda n, v: live() and self._obs_pos(n, v))
        m.observe_property("duration", lambda _n, v: live() and self._sig_dur.emit(int((v or 0) * 1000)))
        m.observe_property("pause", lambda n, v: live() and self._obs_flag(n, v))
        m.observe_property("idle-active", lambda n, v: live() and self._obs_flag(n, v))
        m.observe_property("playlist-pos", lambda _n, v: live() and self._sig_plpos.emit(-1 if v is None else int(v)))
        m.event_callback("end-file")(lambda ev: live() and self._sig_end.emit(*_end_reason(ev)))
        m.event_callback("file-loaded")(lambda _ev: live() and self._sig_loaded.emit())

    def _set_opt(self, opt: str, value: str) -> None:
        """Set an mpv option on the live deck and remember it for new decks."""
        self._opts[opt] = value
        try:
            self._m[opt] = value
        except Exception:
            pass

    def _new_deck(self):
        deck = self._spare
        self._spare = None
        if deck is None:
            deck = self._make_player()
            self._wire(deck)
        for opt, value in self._opts.items():
            try:
                deck[opt] = value
            except Exception:
                pass
        try:
            deck.audio_device = self._device
            deck.command("af", "set", self._chain) if self._chain else deck.command("af", "clr", "")
        except Exception:
            pass
        deck.volume = 0
        deck.pause = False
        return deck

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
        if (autoplay and position_ms == 0 and self._fade_controls and not self._exclusive
                and not self._idle and not self._paused):
            self._crossfade(self._pos, 1200)          # skipping / picking a song while playing: quick blend
            return
        if self._xf is not None:
            self._finish_xf()
        if autoplay and position_ms == 0:
            position_ms = self._trim_for(track)[0]    # DJ mode: skip a silent intro
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
        self._set_opt("loop-file", "inf" if self._repeat == RepeatMode.ONE else "no")
        if self.current_track is not None and not self._idle:
            self._preload()

    def _stop_at_end(self) -> None:
        self._cmd("stop")
        self._pos_ms = 0
        self.positionChanged.emit(0)

    def play(self) -> None:
        if self.current_track is None:
            return
        if self._fade_anim is not None:
            self._fade_anim.stop()
        if self._idle:                                 # stopped at the end of the queue
            self._fade = 1.0
            self._load_current(autoplay=True, position_ms=self._pos_ms)
            return
        if self._fade_controls:
            self._fade = 0.0
            self._apply_volume()
            self._m.pause = False
            self.fade_to(1.0, 450)                     # smooth fade-in
        else:
            self._fade = 1.0
            self._apply_volume()
            self._m.pause = False

    def pause(self) -> None:
        if self.current_track is None:
            return
        if self._fade_controls and self.is_playing:
            self.fade_to(0.0, 300, then=self._hard_pause)   # smooth fade-out, then pause
        else:
            self._hard_pause()

    def _hard_pause(self) -> None:
        if self._xf is not None:
            self._finish_xf()
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
        """0-100 uses mpv's (perceptual, cubic) volume; 101-200 keeps mpv at 100 and adds the
        equivalent gain as a filter before the limiter, so boosted peaks never clip."""
        p = max(0, min(self.max_volume, int(percent)))
        self._volume = p
        self._apply_volume()
        boost = 60 * math.log10(p / 100) if p > 100 else 0.0     # same cubic curve as mpv: 200% = +18 dB
        if (boost > 0) != (self._boost_sent > 0):
            self._boost_db = boost
            self._rebuild()                  # boost filter added/removed (only when crossing 100%)
        elif boost > 0 and abs(boost - self._boost_sent) > 0.01:
            self._boost_db = boost
            if self._cmd("af-command", "boost", "volume", f"{boost:.2f}dB", "Parsed_volume_0"):
                self._boost_sent = boost
            else:
                self._rebuild()

    def _deck_volume(self, amp: float) -> float:
        # mpv's volume is cubic (perceptual): amplitude a needs volume * a^(1/3)
        return min(self._volume, 100) * max(0.0, min(1.0, amp)) ** (1 / 3)

    def _apply_volume(self) -> None:
        if self._xf is None:
            try:
                self._m.volume = self._deck_volume(self._fade)
            except Exception:
                pass

    def fade_to(self, factor: float, ms: int, then=None) -> None:
        """Smoothly change the master level (0..1): pause/play fades and the sleep timer."""
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._fade))
        anim.setEndValue(float(factor))
        anim.setDuration(max(1, int(ms)))
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.valueChanged.connect(lambda v: (setattr(self, "_fade", float(v)), self._apply_volume()))
        if then:
            anim.finished.connect(then)
        self._fade_anim = anim
        anim.start()

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
        except Exception:
            pass
        self._device = name
        for deck in (self._m, self._fading):
            try:
                if deck is not None and deck.audio_device != name:
                    deck.audio_device = name
            except Exception:
                pass

    def _rebuild(self) -> None:
        chain = build_chain(self._eq, opts=ChainOptions(
            self._boost_db, self._loudness, self._bypass, self._solo, self._meter, self._rta,
            self._level, self._speaker, self._karaoke))
        self._chain = chain
        self._level_sent = self._level
        self._cmd("af", "set", chain) if chain else self._cmd("af", "clr", "")
        self._eq_sent = self._eq.copy()
        self._boost_sent = self._boost_db

    def set_equalizer(self, state: EqState) -> None:
        prev = self._eq_sent
        self._eq = state.copy()
        if (self._solo is not None and prev is not None and prev.studio_on and state.studio_on
                and self._solo < len(state.studio.bands) and len(prev.studio.bands) == len(state.studio.bands)):
            b = state.studio.bands[self._solo]          # sweeping a soloed band: just retune it
            q = b.q if b.type in ("peak", "notch") else 2.0
            if (self._cmd("af-command", "solo", "f", f"{b.freq:.1f}", "Parsed_bandpass_0")
                    and self._cmd("af-command", "solo", "w", f"{max(0.5, min(8.0, q)):.2f}", "Parsed_bandpass_0")):
                self._eq_sent = state.copy()
                return
        if (prev is not None and prev.studio_on and state.studio_on and self._bypass is None
                and self._solo is None):
            cmds = live_commands(prev.studio, state.studio)
            if cmds is not None and all(self._cmd(*c) for c in cmds):
                self._eq_sent = state.copy()
                return
            self._rebuild()
            return
        if prev is not None and prev.enabled and state.enabled and prev.mode == state.mode == "simple":
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
        self._rebuild()

    def set_normalize(self, on: bool) -> None:
        self._replaygain = on
        self._set_opt("replaygain", "track" if on else "no")

    def set_bypass(self, gain_db: float | None) -> None:
        if gain_db != self._bypass:
            self._bypass = gain_db
            self._rebuild()

    def set_meter(self, on: bool) -> None:
        if on != self._meter:
            self._meter = on
            self._rebuild()

    def reset_meter(self) -> None:
        if self._meter:
            self._rebuild()              # a fresh meter starts its integrated reading again

    def meter(self) -> dict | None:
        if not self._meter or self._idle:
            return None
        try:
            md = self._m._get_property("af-metadata/meter")
        except Exception:
            return None
        if not md:
            return None
        def num(key):
            try:
                return float(md.get(f"lavfi.r128.{key}"))
            except (TypeError, ValueError):
                return None
        tp = num("true_peak")
        return {"M": num("M"), "S": num("S"), "I": num("I"), "LRA": num("LRA"),
                "TP": 20 * math.log10(tp) if tp and tp > 0 else None}

    def set_loudness(self, on: bool) -> None:
        if on != self._loudness:
            self._loudness = on
            self._rebuild()

    def set_sound_options(self, exclusive: bool, hq_resample: bool, channels: str) -> None:
        wanted = (exclusive, hq_resample, channels or "auto-safe")
        if getattr(self, "_sound", None) == wanted:
            return
        first = not hasattr(self, "_sound")
        self._sound = wanted
        self._exclusive = exclusive               # two decks can't share an exclusive device: no crossfades
        for opt, value in (("audio-exclusive", "yes" if exclusive else "no"),
                           ("audio-channels", wanted[2]),
                           # libswresample's own high-quality settings (always available, unlike soxr)
                           ("audio-swresample-o", "filter_size=64,phase_shift=12,cutoff=0.97" if hq_resample else "")):
            self._set_opt(opt, value)
        if not first and not self._idle:
            self._cmd("ao-reload")           # reopen the output with the new settings

    def _prop(self, name: str):
        try:
            return getattr(self._m, name.replace("-", "_"))
        except Exception:
            return None

    def audio_info(self) -> dict | None:
        if self._idle or self.current_track is None:
            return None
        processing = []
        if self._loudness:
            processing.append("loudness levelling")
        if self._level is not None:
            processing.append(f"library levelling {self._level:+.1f} dB")
        if self._eq.studio_on:
            processing.append("studio EQ (bypassed for A/B)" if self._bypass is not None else "studio EQ")
            if self._bypass is None:
                processing += self._eq.studio.tools.active_names()
                if self._eq.studio.crossfeed:
                    processing.append("crossfeed")
        elif self._eq.enabled:
            processing.append("equalizer")
        if self._speaker is not None and self._speaker.describe():
            processing.append(self._speaker.describe())
        if self._boost_db > 0:
            processing.append(f"volume boost {self._volume}%")
        if self._loudness or self._eq.enabled or self._boost_db > 0:
            processing.append("limiter")
        if self._meter:
            processing.append("loudness meter")
        if self._volume < 100:
            processing.append(f"volume {self._volume}%")
        if self._replaygain:
            processing.append("ReplayGain")
        try:
            exclusive = bool(self._m["audio-exclusive"])
        except Exception:
            exclusive = False
        return {"codec": self._prop("audio-codec-name"), "in": self._prop("audio-params") or {},
                "out": self._prop("audio-out-params") or {}, "ao": self._prop("current-ao") or "",
                "exclusive": exclusive, "processing": processing}

    # ---- DJ mode -----------------------------------------------------------------
    def _on_dur(self, ms: int) -> None:
        self._dur_ms = ms

    def set_dj(self, crossfade_ms: int, trim_silence: bool, fade_controls: bool, keep_albums: bool) -> None:
        self._xf_ms, self._trim = max(0, int(crossfade_ms)), bool(trim_silence)
        self._fade_controls, self._keep_albums = bool(fade_controls), bool(keep_albums)
        if self._xf_ms > 0 or self._trim:
            self._dj_timer.start()
        else:
            self._dj_timer.stop()

    def set_karaoke(self, vocal: float, semitones: int) -> None:
        k = (round(max(0.0, min(1.0, vocal)), 2), int(max(-12, min(12, semitones))))
        if k != self._karaoke:
            self._karaoke = k
            self._rebuild()

    def _trim_for(self, track: dict | None) -> tuple[int, int | None]:
        """(intro silence to skip, where trailing silence starts) in ms, when trimming is on."""
        if not self._trim or not track or self.trim_lookup is None:
            return 0, None
        try:
            r = self.trim_lookup(track["path"])
        except Exception:
            r = None
        return r if r else (0, None)

    def _same_album_flow(self, a: dict, b: dict) -> bool:
        """Consecutive tracks of one album (live albums, DJ mixes) stay gapless instead of crossfading."""
        if not self._keep_albums or not a or not b:
            return False
        if a.get("album") in ("", "Unknown Album") or a.get("album") != b.get("album"):
            return False
        aa = a.get("album_artist") or a.get("artist")
        return aa == (b.get("album_artist") or b.get("artist")) and b.get("track_no", 0) == a.get("track_no", 0) + 1

    def _dj_check(self) -> None:
        if self._xf is not None or self._idle or self._paused or self.current_track is None:
            return
        if self._repeat == RepeatMode.ONE or self._pos + 1 >= len(self._order):
            return
        cur, nxt = self.current_track, self._queue[self._order[self._pos + 1]]
        dur = self._dur_ms or cur.get("duration_ms", 0)
        if dur <= 0:
            return
        _intro, outro = self._trim_for(cur)
        end = min(dur, outro) if outro else dur
        pos = self.precise_position
        if pos < 1000:
            return
        flow = self._same_album_flow(cur, nxt)
        xf = 0 if (flow or self._exclusive) else self._xf_ms
        if xf > 0 and end - pos <= xf:
            self._crossfade(self._pos + 1, max(500, min(xf, end - pos)))
        elif xf == 0 and outro and not flow and pos >= end:
            self.next(auto=True)                       # only silence left: move on

    def _crossfade(self, order_pos: int, ms: int, start_ms: int | None = None) -> None:
        """Start the track at `order_pos` on the spare deck and blend into it over `ms`."""
        if self._xf is not None:
            self._finish_xf()
        old = self._m
        deck = self._new_deck()
        self._fading = old
        self._m = deck                                 # promote: observers now report the new deck
        self._pos = order_pos
        track = self.current_track
        start = self._trim_for(track)[0] if start_ms is None else start_ms
        self._pending_seek = start if start > 0 else None
        self._pos_ms, self._last_emit, self._dur_ms = start, -10_000, track.get("duration_ms", 0)
        self._paused, self._idle = False, False
        self._preloaded = None
        self._err_streak = 0
        self._cmd("loadfile", track["path"], "replace")
        self._preload()
        self._xf = {"old": old, "t0": time.monotonic(), "ms": max(1, ms)}
        self._xf_step()
        self._xf_timer.start()
        self.trackChanged.emit(track)
        self.positionChanged.emit(self._pos_ms)
        self.playingChanged.emit(True)
        self.stateChanged.emit("playing")

    def _xf_step(self) -> None:
        x = self._xf
        if x is None:
            self._xf_timer.stop()
            return
        t = min(1.0, (time.monotonic() - x["t0"]) * 1000 / x["ms"])
        try:   # equal-power curves: the sum stays equally loud through the blend
            x["old"].volume = self._deck_volume(math.cos(t * math.pi / 2) * self._fade)
            self._m.volume = self._deck_volume(math.sin(t * math.pi / 2) * self._fade)
        except Exception:
            pass
        if t >= 1.0:
            self._finish_xf()

    def _finish_xf(self) -> None:
        x, self._xf = self._xf, None
        self._xf_timer.stop()
        if x is not None:
            old = x["old"]
            try:
                old.command("stop")
                old.volume = 0
            except Exception:
                pass
            self._fading = None
            self._spare = old                          # reused for the next crossfade
        self._apply_volume()

    @property
    def crossfading(self) -> bool:
        return self._xf is not None

    def shutdown(self) -> None:
        self._xf_timer.stop()
        self._dj_timer.stop()
        for deck in (self._m, self._fading, self._spare):
            try:
                if deck is not None:
                    deck.terminate()
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
