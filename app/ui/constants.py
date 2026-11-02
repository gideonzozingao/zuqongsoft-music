"""Sizes, breakpoints and the fixed library views shared by the window modules."""
from __future__ import annotations

COVER = 252
DRAWER_W, NP_W = 264, 300
WIDE_MIN, MEDIUM_MIN = 1180, 860      # window widths: wide >= 1180 > medium >= 860 > compact
SONGS = {"kind": "songs"}
STATS = {"kind": "stats", "name": "Your year in music"}
SMART_ICONS = {"most_played": "insights", "recently_added": "add", "recently_played": "repeat",
               "top_rated": "star", "forgotten": "auto_awesome", "never_played": "music_note"}
ALBUMS = {"kind": "albums"}
