"""Zuqongsoft Music main window, split by area:

    window.py         MainWindow: combines the mixins below with QMainWindow
    builders.py       building the panes, bar and menus; signal wiring
    library_views.py  navigation, songs / albums / stats views, folders, scanning
    playlists.py      playlists, context menus, queueing, ratings
    playback.py       now playing, cover, shuffle/repeat, volume, media keys
    extras.py         DJ mode, radio, karaoke, full screen, mini player, tray, sleep timer
    sound.py          sound settings, loudness levelling and scanning
    eq_control.py     equalizer profiles, automatic EQ, the EQ window
    devices.py        audio output devices and Bluetooth
    layout.py         responsive layout and the event filter
    theming.py        themes and live re-theming
    session.py        settings, session save/restore, shutdown
    widgets.py        small widgets and helpers
    constants.py      sizes, breakpoints, fixed views
    launch.py         run(): start-up
"""
from .constants import ALBUMS, SONGS, STATS
from .launch import resource, run
from .window import MainWindow

__all__ = ["ALBUMS", "SONGS", "STATS", "MainWindow", "resource", "run"]
