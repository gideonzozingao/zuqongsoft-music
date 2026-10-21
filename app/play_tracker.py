"""Counts plays and skips from what the engine actually played.

A play counts once you've *listened* (wall-clock, so seeking ahead doesn't count) to
half the song or 4 minutes, whichever comes first (the Last.fm rule); very short
songs need 80%. Leaving a song earlier counts as a skip. Repeat-one loops count
each time round.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal


def counts_as_play(listened_ms: int, duration_ms: int) -> bool:
    if duration_ms <= 0:
        return listened_ms >= 30_000
    if duration_ms <= 30_000:
        return listened_ms >= duration_ms * 0.8
    return listened_ms >= min(duration_ms / 2, 240_000)


class PlayTracker(QObject):
    recorded = Signal(str, bool)        # path, counted as a play

    def __init__(self, engine, library, parent=None) -> None:
        super().__init__(parent)
        self.engine, self.library = engine, library
        self._track: dict | None = None
        self._listened = 0.0
        self._since: float | None = None      # monotonic time playing started, while playing
        self._started_at: float | None = None
        self._playing = False
        self._last_pos = 0
        engine.trackChanged.connect(self._on_track)
        engine.playingChanged.connect(self._on_playing)
        engine.positionChanged.connect(self._on_position)

    def _accumulate(self) -> None:
        if self._since is not None:
            now = time.monotonic()
            self._listened += (now - self._since) * 1000
            self._since = now if self._playing else None

    def _on_playing(self, playing: bool) -> None:
        self._accumulate()
        self._playing = playing
        if playing:
            self._since = time.monotonic()
            if self._started_at is None:
                self._started_at = time.time()
        else:
            self._since = None

    def _on_position(self, ms: int) -> None:
        t = self._track
        if t and self._last_pos > (t.get("duration_ms") or 0) - 4000 > 0 and ms + 4000 < self._last_pos:
            self.finish()                   # looped back to the start (repeat one)
            self._begin(t)
        self._last_pos = ms

    def _begin(self, track: dict | None) -> None:
        self._track = track
        self._listened = 0.0
        self._last_pos = 0
        self._since = time.monotonic() if self._playing else None
        self._started_at = time.time() if self._playing else None

    def _on_track(self, track: dict) -> None:
        self.finish()
        self._begin(track)

    def finish(self, closing: bool = False) -> None:
        """Record the current song (called on track change and when the app closes)."""
        t = self._track
        if t is None:
            return
        self._accumulate()
        listened = int(self._listened)
        counted = counts_as_play(listened, t.get("duration_ms") or 0)
        skipped = not counted and listened >= 500 and not closing
        self._track = None
        if counted or skipped:
            try:
                self.library.record_play(t, self._started_at or time.time(), listened, counted, skipped)
            except Exception:
                return
            if counted:
                t["play_count"] = t.get("play_count", 0) + 1
                t["last_played"] = self._started_at or time.time()
            else:
                t["skip_count"] = t.get("skip_count", 0) + 1
            self.recorded.emit(t["path"], counted)
