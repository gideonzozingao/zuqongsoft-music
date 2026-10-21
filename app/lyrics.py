"""Lyrics: .lrc files, embedded tags, and LRCLIB (free, no API key) as a fallback.

Synced lyrics are lists of (ms, text); plain lyrics have ms=None. Online results
(and "not found" markers) are cached in the library database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import mutagen
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "MusicPlayer/1.0 (Zuqongtech desktop player)"
RETRY_NOT_FOUND_DAYS = 14

_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_TAG = re.compile(r"^\[[a-zA-Z#]+:.*\]$")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.I)


@dataclass
class Lyrics:
    lines: list[tuple[int | None, str]] = field(default_factory=list)
    synced: bool = False
    source: str = ""            # "lrc file" | "embedded" | "LRCLIB"
    instrumental: bool = False
    file: str = ""              # the .lrc it came from, if any

    @property
    def empty(self) -> bool:
        return not self.instrumental and not any(t.strip() for _ms, t in self.lines)


def parse_lrc(text: str) -> tuple[list[tuple[int | None, str]], bool]:
    """LRC with [mm:ss.xx] stamps (several per line allowed, [offset:] honoured),
    or plain text if there are no stamps."""
    timed: list[tuple[int, str]] = []
    plain: list[str] = []
    offset = 0
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        m = _OFFSET.match(line)
        if m:
            offset = int(m.group(1))
            continue
        stamps, pos = [], 0
        while (m := _STAMP.match(line, pos)):
            frac = m.group(3) or "0"
            ms = int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + int(frac.ljust(3, "0")[:3])
            stamps.append(ms)
            pos = m.end()
        if stamps:
            words = _WORD_STAMP.sub("", line[pos:]).strip()
            timed += [(ms, words) for ms in stamps]
        elif line and not _TAG.match(line):
            plain.append(_WORD_STAMP.sub("", line))
        elif not line and plain:
            plain.append("")
    if timed:
        timed.sort(key=lambda x: x[0])
        # A positive offset means the lyrics should appear earlier.
        return [(max(0, ms - offset), t) for ms, t in timed], True
    while plain and not plain[-1]:
        plain.pop()
    return [(None, t) for t in plain], False


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---- .lrc files -----------------------------------------------------------------------
# Where .lrc files go when the music folder can't be written to (read-only drive, etc.).
# Set by the app at startup (QStandardPaths isn't usable from here without a QApplication).
USER_LRC_DIR: str = ""
_LEADING_STAMPS = re.compile(r"^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]\s*)+")


def fmt_stamp(ms: int) -> str:
    ms = max(0, int(ms))
    return f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}]"


def strip_stamps(line: str) -> str:
    return _LEADING_STAMPS.sub("", line)


def first_stamp(line: str) -> int | None:
    m = _STAMP.match(line.lstrip())
    if not m:
        return None
    frac = m.group(3) or "0"
    return int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + int(frac.ljust(3, "0")[:3])


def to_lrc(lines: list[tuple[int | None, str]]) -> str:
    """Lines as LRC text: '[mm:ss.xx]text' for synced lines, plain text otherwise."""
    return "\n".join(f"{fmt_stamp(ms)}{t}" if ms is not None else t for ms, t in lines)


def _user_lrc_path(audio_path: str) -> str:
    key = hashlib.sha1(os.path.normcase(os.path.abspath(audio_path)).encode("utf-8")).hexdigest()
    return os.path.join(USER_LRC_DIR, key + ".lrc") if USER_LRC_DIR else ""


def lrc_path_for(audio_path: str) -> str:
    """The .lrc beside the song (reusing an existing file's spelling, e.g. SONG.LRC)."""
    folder, base = os.path.split(audio_path)
    stem = os.path.splitext(base)[0]
    try:
        for n in os.listdir(folder):
            if n.lower() == stem.lower() + ".lrc":
                return os.path.join(folder, n)
    except OSError:
        pass
    return os.path.join(folder, stem + ".lrc")


def existing_lrc(audio_path: str) -> str:
    """Path of the .lrc that would be loaded for this song, or ''."""
    beside = lrc_path_for(audio_path)
    if os.path.isfile(beside):
        return beside
    fallback = _user_lrc_path(audio_path)
    return fallback if fallback and os.path.isfile(fallback) else ""


def save_lrc(track: dict, body: str) -> tuple[str, bool]:
    """Write lyrics for `track` as an .lrc next to the song, adding title/artist/album tags.
    Falls back to the app's own lyrics folder if the music folder is read-only.
    Returns (path written, used_fallback)."""
    header = [f"[ti:{track.get('title', '')}]", f"[ar:{track.get('artist', '')}]",
              f"[al:{track.get('album', '')}]"]
    if track.get("duration_ms"):
        d = track["duration_ms"] // 1000
        header.append(f"[length:{d // 60:02d}:{d % 60:02d}]")
    header.append("[re:Zuqongsoft Music]")
    kept = [ln for ln in body.replace("\r\n", "\n").split("\n") if not _TAG.match(ln.strip())]
    text = "\n".join(header + kept).rstrip() + "\n"
    target = lrc_path_for(track["path"])
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        stale = _user_lrc_path(track["path"])
        if stale and os.path.isfile(stale):
            os.remove(stale)          # the file beside the song wins from now on
        return target, False
    except OSError:
        fallback = _user_lrc_path(track["path"])
        if not fallback:
            raise
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        with open(fallback, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return fallback, True


def read_lyrics_file(path: str) -> str:
    """Text of an .lrc or .txt file, whatever its encoding."""
    return _read_text(path)


def from_lrc_file(audio_path: str) -> Lyrics | None:
    path = existing_lrc(audio_path)
    if not path:
        return None
    lines, synced = parse_lrc(_read_text(path))
    return Lyrics(lines, synced, "lrc file", file=path) if lines else None


def from_tags(audio_path: str) -> Lyrics | None:
    try:
        f = mutagen.File(audio_path)
    except Exception:
        return None
    tags = getattr(f, "tags", None) if f is not None else None
    if not tags:
        return None
    texts: list[str] = []
    if hasattr(tags, "getall"):                                        # ID3 (mp3, wav, aiff)
        for sylt in tags.getall("SYLT"):
            if getattr(sylt, "format", 2) == 2 and sylt.text:          # 2 = milliseconds
                lines = sorted((int(ms), str(t).strip()) for t, ms in sylt.text)
                return Lyrics(lines, True, "embedded")
        texts += [str(u.text) for u in tags.getall("USLT") if getattr(u, "text", "")]
    else:
        for key in ("SYNCEDLYRICS", "LYRICS", "UNSYNCEDLYRICS", "syncedlyrics", "lyrics",
                    "unsyncedlyrics", "\xa9lyr", "WM/Lyrics"):
            try:
                value = tags.get(key)
            except Exception:
                value = None
            if value:
                texts += [str(v) for v in (value if isinstance(value, list) else [value])]
    for text in texts:                     # prefer a synced one if any text has stamps
        lines, synced = parse_lrc(text)
        if synced:
            return Lyrics(lines, True, "embedded")
    for text in texts:
        lines, synced = parse_lrc(text)
        if lines:
            return Lyrics(lines, False, "embedded")
    return None


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _from_lrclib_record(rec: dict) -> Lyrics:
    if rec.get("instrumental"):
        return Lyrics([], False, "LRCLIB", instrumental=True)
    if rec.get("syncedLyrics"):
        lines, synced = parse_lrc(rec["syncedLyrics"])
        if lines:
            return Lyrics(lines, synced, "LRCLIB")
    lines, _ = parse_lrc(rec.get("plainLyrics") or "")
    return Lyrics(lines, False, "LRCLIB")


def from_lrclib(track: dict) -> Lyrics | None:
    """Exact match on artist/title/album/duration first, then a search. Raises on network errors."""
    artist, title = track.get("artist", ""), track.get("title", "")
    if not title or artist in ("", "Unknown Artist"):
        return None
    secs = round((track.get("duration_ms") or 0) / 1000)
    q = {"artist_name": artist, "track_name": title, "album_name": track.get("album", "")}
    if secs:
        q["duration"] = secs
    rec = _get_json(f"{LRCLIB}/get?" + urllib.parse.urlencode(q))
    if rec:
        return _from_lrclib_record(rec)
    results = _get_json(f"{LRCLIB}/search?" + urllib.parse.urlencode({"artist_name": artist, "track_name": title}))
    if not results:
        return None
    close = [r for r in results if not secs or abs((r.get("duration") or 0) - secs) <= 4]
    close.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - secs)))
    return _from_lrclib_record(close[0]) if close else None


# ---- background loader ---------------------------------------------------------------
class _Signals(QObject):
    done = Signal(str, object, str)          # path, Lyrics | None, status


class _Job(QRunnable):
    def __init__(self, track: dict, library, online: bool, signals: _Signals) -> None:
        super().__init__()
        self.track, self.library, self.online, self.signals = track, library, online, signals

    def run(self) -> None:
        path = self.track["path"]
        try:
            lyr = from_lrc_file(path) or from_tags(path)
            if lyr is None:
                cached = self.library.cached_lyrics(path)
                if cached:
                    if cached["source"] == "none":
                        fresh = time.time() - cached["fetched"] < RETRY_NOT_FOUND_DAYS * 86400
                        if fresh or not self.online:
                            self.signals.done.emit(path, None, "none")
                            return
                    elif cached["source"] == "instrumental":
                        self.signals.done.emit(path, Lyrics([], False, "LRCLIB", True), "ok")
                        return
                    else:
                        lines, synced = parse_lrc(cached["body"])
                        self.signals.done.emit(path, Lyrics(lines, synced, cached["source"]), "ok")
                        return
                if not self.online:
                    self.signals.done.emit(path, None, "offline")
                    return
                self.signals.done.emit(path, None, "searching")
                lyr = from_lrclib(self.track)
                if lyr is None or lyr.empty and not lyr.instrumental:
                    self.library.cache_lyrics(path, "none", False, "")
                    self.signals.done.emit(path, None, "none")
                    return
                body = "\n".join(f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}]{t}"
                                 if ms is not None else t for ms, t in lyr.lines)
                self.library.cache_lyrics(path, "instrumental" if lyr.instrumental else "LRCLIB",
                                          lyr.synced, body)
            self.signals.done.emit(path, lyr, "ok")
        except Exception as exc:                    # network down etc.: try again next time
            self.signals.done.emit(path, None, f"error:{exc}")


class LyricsLoader(QObject):
    """Emits loaded(path, Lyrics | None, status). status: ok, searching, none, offline, error:..."""
    loaded = Signal(str, object, str)

    def __init__(self, library, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _Signals(self)
        self._signals.done.connect(self.loaded)

    def request(self, track: dict, online: bool) -> None:
        self._pool.start(_Job(track, self.library, online, self._signals))

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(1500)
