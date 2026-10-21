"""Music library: SQLite storage, mutagen tag reading, playlists, background folder scanner."""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import mutagen
from PySide6.QtCore import QThread, Signal

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma"}
COVER_NAMES = ("cover", "folder", "front", "album")
COVER_EXTS = (".jpg", ".jpeg", ".png")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    album        TEXT NOT NULL,
    album_artist TEXT NOT NULL DEFAULT '',
    disc_no      INTEGER NOT NULL DEFAULT 0,
    track_no     INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0,
    added        REAL NOT NULL DEFAULT 0
);
-- Listening stats are keyed by path so they survive rescans.
CREATE TABLE IF NOT EXISTS track_stats (
    path        TEXT PRIMARY KEY,
    play_count  INTEGER NOT NULL DEFAULT 0,
    skip_count  INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    rating      INTEGER NOT NULL DEFAULT 0
);
-- One row per counted play; title etc. are copied so history outlives the file.
CREATE TABLE IF NOT EXISTS plays (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    album        TEXT NOT NULL,
    album_artist TEXT NOT NULL,
    played_at    REAL NOT NULL,
    ms_played    INTEGER NOT NULL
);
-- Lyrics found online (or a "none" marker so we don't ask again every time).
CREATE TABLE IF NOT EXISTS lyrics (
    path    TEXT PRIMARY KEY,
    source  TEXT NOT NULL,
    synced  INTEGER NOT NULL,
    body    TEXT NOT NULL,
    fetched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
-- Entries reference tracks by path, so they survive a folder being removed and re-added.
CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    position    INTEGER NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tracks_sort ON tracks(artist, album, disc_no, track_no, title);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album, album_artist);
CREATE INDEX IF NOT EXISTS idx_playlist_items ON playlist_items(playlist_id, position);
CREATE INDEX IF NOT EXISTS idx_tracks_added ON tracks(added);
CREATE INDEX IF NOT EXISTS idx_plays_time ON plays(played_at);
"""

UPSERT = """
INSERT INTO tracks (path, title, artist, album, album_artist, disc_no, track_no, duration_ms, mtime, added)
VALUES (:path, :title, :artist, :album, :album_artist, :disc_no, :track_no, :duration_ms, :mtime, :added)
ON CONFLICT(path) DO UPDATE SET
    title=excluded.title, artist=excluded.artist, album=excluded.album,
    album_artist=excluded.album_artist, disc_no=excluded.disc_no, track_no=excluded.track_no,
    duration_ms=excluded.duration_ms, mtime=excluded.mtime
"""

# Default order: artist -> album -> disc -> track number.
TRACK_ORDER = (" ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.disc_no, t.track_no,"
               " t.title COLLATE NOCASE")
STATS_COLS = ("COALESCE(s.rating, 0) AS rating, COALESCE(s.play_count, 0) AS play_count,"
              " COALESCE(s.skip_count, 0) AS skip_count, s.last_played AS last_played")
TRACK_SELECT = f"SELECT t.*, {STATS_COLS} FROM tracks t LEFT JOIN track_stats s ON s.path = t.path"
T_COLS = ("t.title", "t.artist", "t.album")
DAY = 86400.0

# Smart playlists: id -> (name, where, order, limit). "?" in where = cut-off timestamp.
SMART = {
    "most_played": ("Most Played", "COALESCE(s.play_count, 0) > 0",
                    "s.play_count DESC, s.last_played DESC", 100),
    "recently_added": ("Recently Added", "", "t.added DESC, t.path", 100),
    "recently_played": ("Recently Played", "s.last_played IS NOT NULL", "s.last_played DESC", 100),
    "top_rated": ("Top Rated", "COALESCE(s.rating, 0) >= 4",
                  "s.rating DESC, s.play_count DESC", 200),
    "forgotten": ("Forgotten Favourites", "s.play_count >= 3 AND s.last_played < ?",
                  "s.play_count DESC", 100),
    "never_played": ("Never Played", "COALESCE(s.play_count, 0) = 0", "t.added DESC, t.path", 500),
}
FORGOTTEN_DAYS = 60
ALBUM_ARTIST = "COALESCE(NULLIF(album_artist, ''), artist)"


def _prefix(root: str) -> str:
    return root if root.endswith(os.sep) else root + os.sep


def _filter(text: str, cols=("title", "artist", "album")) -> tuple[str, list]:
    """Every whitespace-separated term must match at least one column."""
    terms = text.split()
    if not terms:
        return "", []
    clause = "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")"
    params = []
    for t in terms:
        like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [like] * len(cols)
    return " AND ".join([clause] * len(terms)), params


# ---- tags ------------------------------------------------------------------
# Formats without an "easy" mapping (e.g. ID3 inside WAV) expose raw ID3 frames.
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "albumartist": "TPE2",
              "album": "TALB", "tracknumber": "TRCK", "discnumber": "TPOS"}


def _first(audio, key: str) -> str:
    try:
        value = audio.get(key)
    except Exception:
        value = None
    if not value:
        frame = None
        try:
            tags = getattr(audio, "tags", None)
            frame = tags.get(ID3_FRAMES[key]) if tags is not None and key in ID3_FRAMES else None
        except Exception:
            pass
        text = getattr(frame, "text", None)
        return str(text[0]).strip() if text else ""
    if isinstance(value, (list, tuple)):
        value = value[0]
    return str(value).strip()


def _num(text: str) -> int:
    try:
        return int(text.split("/")[0])
    except ValueError:
        return 0


def read_tags(path: str, mtime: float) -> dict:
    stem = os.path.splitext(os.path.basename(path))[0]
    info = {
        "path": path, "title": stem, "artist": "Unknown Artist", "album": "Unknown Album",
        "album_artist": "", "disc_no": 0, "track_no": 0, "duration_ms": 0, "mtime": mtime,
    }
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return info
    if audio is None:
        return info
    try:
        info["duration_ms"] = int((audio.info.length or 0) * 1000)
    except Exception:
        pass
    album_artist = _first(audio, "albumartist")
    info["title"] = _first(audio, "title") or stem
    info["artist"] = _first(audio, "artist") or album_artist or info["artist"]
    info["album"] = _first(audio, "album") or info["album"]
    info["album_artist"] = album_artist
    info["track_no"] = _num(_first(audio, "tracknumber"))
    info["disc_no"] = _num(_first(audio, "discnumber"))
    return info


def read_cover(path: str) -> bytes | None:
    """Embedded art (ID3 / FLAC / MP4), falling back to cover.jpg etc. in the folder."""
    try:
        f = mutagen.File(path)
        if f is not None:
            pictures = getattr(f, "pictures", None)          # FLAC
            if pictures:
                return pictures[0].data
            tags = f.tags
            if tags is not None:
                if hasattr(tags, "getall"):                   # ID3
                    apics = tags.getall("APIC")
                    if apics:
                        return apics[0].data
                elif "covr" in tags and tags["covr"]:         # MP4
                    return bytes(tags["covr"][0])
    except Exception:
        pass
    folder = os.path.dirname(path)
    try:
        names = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return None
    for base in COVER_NAMES:
        for ext in COVER_EXTS:
            if base + ext in names:
                try:
                    return Path(folder, names[base + ext]).read_bytes()
                except OSError:
                    return None
    return None


# ---- M3U ---------------------------------------------------------------------
def write_m3u(path: str, rows: list[dict]) -> None:
    lines = ["#EXTM3U"]
    for t in rows:
        lines.append(f"#EXTINF:{t['duration_ms'] // 1000},{t['artist']} - {t['title']}")
        lines.append(t["path"])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_m3u(path: str) -> list[str]:
    base = os.path.dirname(os.path.abspath(path))
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("file://"):
            from urllib.parse import unquote, urlparse
            line = unquote(urlparse(line).path)
            if os.name == "nt" and line.startswith("/"):
                line = line[1:]
        if not os.path.isabs(line):
            line = os.path.join(base, line)
        out.append(os.path.normpath(line))
    return out


# ---- storage ---------------------------------------------------------------
class Library:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        with self._db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            self._migrate(c)
            c.executescript(INDEXES)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tracks)")}
        added = False
        for name, ddl in (("album_artist", "TEXT NOT NULL DEFAULT ''"),
                          ("disc_no", "INTEGER NOT NULL DEFAULT 0")):
            if name not in cols:
                c.execute(f"ALTER TABLE tracks ADD COLUMN {name} {ddl}")
                added = True
        if "added" not in cols:
            c.execute("ALTER TABLE tracks ADD COLUMN added REAL NOT NULL DEFAULT 0")
            # Best guess for existing tracks: when the file last changed.
            c.execute("UPDATE tracks SET added = CASE WHEN mtime > 0 THEN mtime"
                      " ELSE strftime('%s', 'now') END")
        if added:
            # Force the next scan to re-read tags so the new columns get filled.
            c.execute("UPDATE tracks SET mtime = 0")
            c.execute("DROP INDEX IF EXISTS idx_tracks_sort")

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _rows(self, sql: str, params=()) -> list[dict]:
        with self._db() as c:
            return [dict(r) for r in c.execute(sql, params)]

    # -- tracks / albums --
    def search(self, text: str = "") -> list[dict]:
        where, params = _filter(text, T_COLS)
        return self._rows(TRACK_SELECT + (f" WHERE {where}" if where else "") + TRACK_ORDER, params)

    def albums(self, text: str = "") -> list[dict]:
        where, params = _filter(text)
        sql = (f"SELECT album, {ALBUM_ARTIST} AS aa, COUNT(*) AS tracks, SUM(duration_ms) AS duration_ms,"
               " MIN(path) AS path FROM tracks"
               + (f" WHERE {where}" if where else "")
               + " GROUP BY album, aa ORDER BY aa COLLATE NOCASE, album COLLATE NOCASE")
        return [{"album": r["album"], "artist": r["aa"], "tracks": r["tracks"],
                 "duration_ms": r["duration_ms"] or 0, "path": r["path"]} for r in self._rows(sql, params)]

    def album_tracks(self, album: str, artist: str, text: str = "") -> list[dict]:
        where, params = _filter(text, T_COLS)
        sql = (TRACK_SELECT + " WHERE t.album = ? AND COALESCE(NULLIF(t.album_artist, ''), t.artist) = ?"
               + (f" AND {where}" if where else "")
               + " ORDER BY t.disc_no, t.track_no, t.title COLLATE NOCASE, t.path")
        return self._rows(sql, [album, artist] + params)

    def known_mtimes(self) -> dict[str, float]:
        with self._db() as c:
            return {r["path"]: r["mtime"] for r in c.execute("SELECT path, mtime FROM tracks")}

    def upsert_many(self, rows: list[dict]) -> None:
        for r in rows:
            r.setdefault("added", r.get("mtime") or time.time())
        if rows:
            with self._db() as c:
                c.executemany(UPSERT, rows)

    def remove_missing(self, root: str, seen: set[str]) -> int:
        prefix = _prefix(root)
        with self._db() as c:
            rows = c.execute("SELECT id, path FROM tracks").fetchall()
            gone = [(r["id"],) for r in rows if r["path"].startswith(prefix) and r["path"] not in seen]
            c.executemany("DELETE FROM tracks WHERE id=?", gone)
        return len(gone)

    def remove_under(self, root: str) -> int:
        return self.remove_missing(os.path.normpath(root), set())

    # -- smart playlists, stats, ratings --
    def smart_playlist(self, key: str, text: str = "") -> list[dict]:
        _name, where, order, limit = SMART[key]
        clauses, params = ([where] if where else []), []
        if "?" in where:
            params.append(time.time() - FORGOTTEN_DAYS * DAY)
        fw, fp = _filter(text, T_COLS)
        if fw:
            clauses.append(fw)
            params += fp
        sql = (TRACK_SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "")
               + f" ORDER BY {order} LIMIT {int(limit)}")
        return self._rows(sql, params)

    def tracks_by_paths(self, paths: list[str]) -> list[dict]:
        if not paths:
            return []
        rows = self._rows(TRACK_SELECT + f" WHERE t.path IN ({','.join('?' * len(paths))})", list(paths))
        by_path = {r["path"]: r for r in rows}
        return [by_path[p] for p in paths if p in by_path]

    def set_rating(self, path: str, rating: int) -> None:
        with self._db() as c:
            c.execute("INSERT INTO track_stats (path, rating) VALUES (?, ?)"
                      " ON CONFLICT(path) DO UPDATE SET rating = excluded.rating",
                      (path, max(0, min(5, int(rating)))))

    def record_play(self, t: dict, played_at: float, ms_played: int, counted: bool, skipped: bool) -> None:
        with self._db() as c:
            if counted:
                c.execute("INSERT INTO plays (path, title, artist, album, album_artist, played_at, ms_played)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (t["path"], t["title"], t["artist"], t["album"],
                           t.get("album_artist") or t["artist"], played_at, int(ms_played)))
                c.execute("INSERT INTO track_stats (path, play_count, last_played) VALUES (?, 1, ?)"
                          " ON CONFLICT(path) DO UPDATE SET play_count = play_count + 1,"
                          " last_played = excluded.last_played", (t["path"], played_at))
            elif skipped:
                c.execute("INSERT INTO track_stats (path, skip_count) VALUES (?, 1)"
                          " ON CONFLICT(path) DO UPDATE SET skip_count = skip_count + 1", (t["path"],))

    def years_with_plays(self) -> list[int]:
        rows = self._rows("SELECT DISTINCT CAST(strftime('%Y', played_at, 'unixepoch', 'localtime') AS INTEGER)"
                          " AS y FROM plays ORDER BY y DESC")
        return [r["y"] for r in rows if r["y"]]

    def year_summary(self, year: int) -> dict:
        Y = "strftime('%Y', played_at, 'unixepoch', 'localtime') = ?"
        y = (str(year),)
        with self._db() as c:
            one = lambda sql: c.execute(sql, y).fetchone()  # noqa: E731
            many = lambda sql: [dict(r) for r in c.execute(sql, y)]  # noqa: E731
            totals = one(f"SELECT COUNT(*) AS plays, COALESCE(SUM(ms_played), 0) AS ms,"
                         f" COUNT(DISTINCT path) AS songs, COUNT(DISTINCT artist) AS artists FROM plays WHERE {Y}")
            return {
                "year": year,
                "plays": totals["plays"], "ms": totals["ms"],
                "songs": totals["songs"], "artists": totals["artists"],
                "top_tracks": many(f"SELECT title, artist, album, path, COUNT(*) AS plays, SUM(ms_played) AS ms"
                                   f" FROM plays WHERE {Y} GROUP BY path ORDER BY plays DESC, ms DESC LIMIT 10"),
                "top_artists": many(f"SELECT artist, COUNT(*) AS plays, SUM(ms_played) AS ms FROM plays"
                                    f" WHERE {Y} GROUP BY artist ORDER BY ms DESC LIMIT 5"),
                "top_albums": many(f"SELECT album, album_artist AS artist, MIN(path) AS path, COUNT(*) AS plays,"
                                   f" SUM(ms_played) AS ms FROM plays WHERE {Y}"
                                   f" GROUP BY album, album_artist ORDER BY ms DESC LIMIT 5"),
                "months": {int(r["m"]): r["ms"] for r in many(
                    f"SELECT strftime('%m', played_at, 'unixepoch', 'localtime') AS m, SUM(ms_played) AS ms"
                    f" FROM plays WHERE {Y} GROUP BY m")},
                "best_day": (many(f"SELECT date(played_at, 'unixepoch', 'localtime') AS day, SUM(ms_played) AS ms"
                                  f" FROM plays WHERE {Y} GROUP BY day ORDER BY ms DESC LIMIT 1") or [None])[0],
            }

    # -- lyrics cache --
    def cached_lyrics(self, path: str) -> dict | None:
        rows = self._rows("SELECT * FROM lyrics WHERE path = ?", (path,))
        return rows[0] if rows else None

    def cache_lyrics(self, path: str, source: str, synced: bool, body: str) -> None:
        with self._db() as c:
            c.execute("INSERT OR REPLACE INTO lyrics (path, source, synced, body, fetched) VALUES (?, ?, ?, ?, ?)",
                      (path, source, int(synced), body, time.time()))

    def forget_lyrics(self, path: str) -> None:
        with self._db() as c:
            c.execute("DELETE FROM lyrics WHERE path = ?", (path,))

    # -- playlists --
    def playlists(self) -> list[dict]:
        return self._rows(
            "SELECT p.id, p.name, COUNT(t.id) AS tracks FROM playlists p"
            " LEFT JOIN playlist_items i ON i.playlist_id = p.id"
            " LEFT JOIN tracks t ON t.path = i.path"
            " GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.id")

    def create_playlist(self, name: str) -> int:
        with self._db() as c:
            return c.execute("INSERT INTO playlists (name) VALUES (?)", (name,)).lastrowid

    def rename_playlist(self, pid: int, name: str) -> None:
        with self._db() as c:
            c.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, pid))

    def delete_playlist(self, pid: int) -> None:
        with self._db() as c:
            c.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
            c.execute("DELETE FROM playlists WHERE id = ?", (pid,))

    def playlist_tracks(self, pid: int, text: str = "") -> list[dict]:
        where, params = _filter(text, ("t.title", "t.artist", "t.album"))
        sql = (f"SELECT t.*, i.id AS entry_id, {STATS_COLS} FROM playlist_items i"
               " JOIN tracks t ON t.path = i.path LEFT JOIN track_stats s ON s.path = t.path"
               " WHERE i.playlist_id = ?" + (f" AND {where}" if where else "")
               + " ORDER BY i.position, i.id")
        return self._rows(sql, [pid] + params)

    def add_to_playlist(self, pid: int, paths: list[str]) -> int:
        """Append paths not already in the playlist; returns how many were added."""
        with self._db() as c:
            existing = {r[0] for r in c.execute("SELECT path FROM playlist_items WHERE playlist_id = ?", (pid,))}
            pos = c.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM playlist_items WHERE playlist_id = ?",
                            (pid,)).fetchone()[0]
            new = []
            for p in paths:
                if p not in existing:
                    existing.add(p)
                    new.append((pid, p, pos))
                    pos += 1
            c.executemany("INSERT INTO playlist_items (playlist_id, path, position) VALUES (?, ?, ?)", new)
        return len(new)

    def remove_entries(self, pid: int, entry_ids: list[int]) -> None:
        with self._db() as c:
            c.executemany("DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?",
                          [(e, pid) for e in entry_ids])

    def move_entry(self, pid: int, entry_id: int, delta: int) -> bool:
        """Swap an entry with its visible neighbour (entries whose file is gone are skipped)."""
        with self._db() as c:
            full = [r[0] for r in c.execute(
                "SELECT id FROM playlist_items WHERE playlist_id = ? ORDER BY position, id", (pid,))]
            c.executemany("UPDATE playlist_items SET position = ? WHERE id = ?",
                          [(i, e) for i, e in enumerate(full)])
            visible = [r[0] for r in c.execute(
                "SELECT i.id FROM playlist_items i JOIN tracks t ON t.path = i.path"
                " WHERE i.playlist_id = ? ORDER BY i.position", (pid,))]
            if entry_id not in visible:
                return False
            j = visible.index(entry_id) + delta
            if not 0 <= j < len(visible):
                return False
            a, b = full.index(entry_id), full.index(visible[j])
            c.execute("UPDATE playlist_items SET position = ? WHERE id = ?", (b, entry_id))
            c.execute("UPDATE playlist_items SET position = ? WHERE id = ?", (a, visible[j]))
        return True


# ---- background scanner ----------------------------------------------------
class ScanWorker(QThread):
    """Incremental scan: only files whose mtime changed are re-read."""
    progress = Signal(int)     # files seen so far

    def __init__(self, library: Library, roots: list[str], parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.roots = [os.path.normpath(r) for r in roots]
        self.changed = 0
        self.removed = 0

    def run(self) -> None:
        known = self.library.known_mtimes()
        batch: list[dict] = []
        processed = 0
        for root in self.roots:
            if not os.path.isdir(root):      # unplugged drive: don't prune its tracks
                continue
            seen: set[str] = set()
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if self.isInterruptionRequested():
                        self.library.upsert_many(batch)
                        return
                    if os.path.splitext(name)[1].lower() not in AUDIO_EXTS:
                        continue
                    path = os.path.join(dirpath, name)
                    seen.add(path)
                    processed += 1
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if known.get(path) != mtime:
                        info = read_tags(path, mtime)
                        if path not in known:
                            try:
                                info["added"] = os.path.getctime(path) if sys.platform == "win32" else mtime
                            except OSError:
                                info["added"] = time.time()
                        batch.append(info)
                        self.changed += 1
                        if len(batch) >= 100:
                            self.library.upsert_many(batch)
                            batch.clear()
                    if processed % 25 == 0:
                        self.progress.emit(processed)
            self.library.upsert_many(batch)
            batch.clear()
            self.removed += self.library.remove_missing(root, seen)
        self.progress.emit(processed)
