"""Song Radio / Autoplay: picks songs that fit, using only your library and listening history.

Score for each candidate (higher = better fit):
  same artist +3, same album artist +1, same genre +2,
  played together with a seed song (within 20 minutes in your history) up to +4.5,
  rating up to +1.2, often played +0.3/log, often skipped up to -2,
  heard in the last 2 hours -6, plus a little randomness for variety.
Picks are made one at a time, and an artist just picked counts less for the next pick,
so the radio doesn't play five songs by one artist in a row.
"""
from __future__ import annotations

import math
import random
import time
from collections import Counter, defaultdict

COPLAY_WINDOW = 20 * 60        # two plays this close together count as "played together"
RECENT = 2 * 3600


class Radio:
    def __init__(self, library) -> None:
        self.library = library
        self._coplay: dict[str, Counter] = {}
        self._coplay_plays = -1

    def _together(self) -> dict[str, Counter]:
        """Pairs of songs played close together in your history (cached until new plays arrive)."""
        rows = self.library._rows("SELECT path, played_at FROM plays ORDER BY played_at")
        if len(rows) == self._coplay_plays:
            return self._coplay
        pairs: dict[str, Counter] = defaultdict(Counter)
        for a, b in zip(rows, rows[1:]):
            if a["path"] != b["path"] and b["played_at"] - a["played_at"] <= COPLAY_WINDOW:
                pairs[a["path"]][b["path"]] += 1
                pairs[b["path"]][a["path"]] += 1
        self._coplay, self._coplay_plays = pairs, len(rows)
        return pairs

    def pick(self, seeds: list[dict], exclude: set[str], n: int = 10) -> list[dict]:
        if not seeds:
            return []
        tracks = self.library.search("")
        if not tracks:
            return []
        together = self._together()
        artists = {s["artist"].casefold() for s in seeds}
        album_artists = {(s.get("album_artist") or s["artist"]).casefold() for s in seeds}
        genres = {(s.get("genre") or "").casefold() for s in seeds} - {""}
        seed_paths = [s["path"] for s in seeds]
        now = time.time()
        scored = []
        for t in tracks:
            path = t["path"]
            if path in exclude or path in seed_paths:
                continue
            score = random.random() * 1.2
            if t["artist"].casefold() in artists:
                score += 3
            if (t.get("album_artist") or t["artist"]).casefold() in album_artists:
                score += 1
            if genres and (t.get("genre") or "").casefold() in genres:
                score += 2
            score += 1.5 * min(3, sum(together.get(p, {}).get(path, 0) for p in seed_paths))
            score += 1.2 * (t.get("rating", 0) or 0) / 5
            plays, skips = t.get("play_count", 0) or 0, t.get("skip_count", 0) or 0
            score += 0.3 * math.log1p(plays)
            score -= 2.0 * skips / (plays + skips + 2)
            if t.get("last_played") and now - t["last_played"] < RECENT:
                score -= 6
            scored.append([score, t])
        picks: list[dict] = []
        recent_artists: Counter = Counter(s["artist"].casefold() for s in seeds[-2:])
        while scored and len(picks) < n:
            for item in scored:                 # an artist that just played counts less
                item.append(item[0] - 2.5 * recent_artists[item[1]["artist"].casefold()])
            best = max(range(len(scored)), key=lambda i: scored[i][-1])
            choice = scored.pop(best)[1]
            for item in scored:
                del item[2:]
            picks.append(choice)
            recent_artists[choice["artist"].casefold()] += 1
            if len(picks) >= 2:                 # only the last two picks matter for variety
                recent_artists = Counter(p["artist"].casefold() for p in picks[-2:])
        return picks
