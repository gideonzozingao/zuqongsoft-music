"""Main window (QtWidgets) for the music player, styled after Material 3."""
from __future__ import annotations

import html
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QModelIndex, QProcess, QRect, QRectF, QSettings, QSize, QStandardPaths, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter,
                           QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSlider, QStackedWidget, QStyle, QTableView, QToolButton,
                               QVBoxLayout, QWidget)

from . import system_media, theme
from .theme_picker import ThemeDialog, fill_theme_menu
from .audio_devices import (KIND_ICONS, AudioDeviceManager, OutputDevice, open_bluetooth_settings,
                            open_sound_settings)
from .engine import RepeatMode, create_engine
from .equalizer import EqState, EqStore
from .equalizer_ui import EqualizerDialog
from .library import Library, ScanWorker, read_cover, read_m3u, write_m3u
from .library import SMART
from .models import (COL_RATING, COL_TIME, AlbumDelegate, AlbumModel, CoverCache, StarDelegate,
                     TrackModel, fmt, fmt_long, sort_rows)
from .panels import LyricsPanel, QueuePanel, StarBar
from .play_tracker import PlayTracker
from .stats_page import StatsPage
from .theme import C, icon

COVER = 252
SONGS = {"kind": "songs"}
STATS = {"kind": "stats", "name": "Your year in music"}
SMART_ICONS = {"most_played": "insights", "recently_added": "add", "recently_played": "repeat",
               "top_rated": "star", "forgotten": "auto_awesome", "never_played": "music_note"}
ALBUMS = {"kind": "albums"}


# ---- small widgets / helpers ---------------------------------------------------
class JumpSlider(QSlider):
    """Click anywhere to jump; `dragging` is True while the mouse is held."""
    seekRequested = Signal(int)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.dragging = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _value_at(self, event) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(event.position().x()), max(1, self.width()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_at(e))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            self.setValue(self._value_at(e))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.setValue(self._value_at(e))
            self.seekRequested.emit(self.value())
            e.accept()
        else:
            super().mouseReleaseEvent(e)


def icon_button(name: str, tip: str, size: int = 40, icon_size: int = 24,
                role: str = "on_surface_variant") -> QToolButton:
    b = QToolButton()
    theme.bind(b, name, role)
    b.setIconSize(QSize(icon_size, icon_size))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def m3_button(text: str, kind: str = "", icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        theme.bind(b, icon_name, {"filled": "on_primary", "tonal": "on_secondary_container"}.get(kind, "primary"))
        b.setIconSize(QSize(18, 18))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def rounded_cover(src: QPixmap | None, side: int, radius: int, dpr: float) -> QPixmap:
    px = max(1, int(side * dpr))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    p.setClipPath(clip)
    if src is not None and not src.isNull():
        scaled = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
    else:
        p.fillRect(0, 0, px, px, QColor(C["surface_container_highest"]))
        m = int(px * 0.32)
        icon("music_note", C["outline"]).paint(p, QRect(m, m, px - 2 * m, px - 2 * m))
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def add_action(menu: QMenu, icon_name: str | None, text: str, fn, enabled: bool = True) -> QAction:
    act = menu.addAction(icon(icon_name), text) if icon_name else menu.addAction(text)
    act.triggered.connect(lambda _checked=False: fn())
    act.setEnabled(enabled)
    return act


# ---- main window ----------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Music")
        self.resize(1280, 800)
        self.setMinimumSize(980, 600)
        self.setAcceptDrops(True)

        self.settings = QSettings()
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(data_dir / "library.db")
        from . import lyrics as _lyrics
        _lyrics.USER_LRC_DIR = str(data_dir / "lyrics")     # for read-only music folders
        self.engine = create_engine(self)
        self.tracks = TrackModel()
        self.albums = AlbumModel()
        self.covers = CoverCache(parent=self)
        self.devices = AudioDeviceManager(self)
        self._current_out: OutputDevice | None = None
        self._pinned_id = ""            # "" = follow the system default output
        self._pinned_name = ""
        self._auto_switch = True
        self._pause_on_disconnect = True
        self.eq_store = EqStore(self.settings)
        self.eq = EqState()
        self._eq_device: str | None = None
        self._eq_dialog: EqualizerDialog | None = None
        self._cover_src: QPixmap | None = None
        self._theme_dialog: ThemeDialog | None = None
        self._theming = False
        s = self.settings
        self._theme_preset = str(s.value("theme/preset", theme.DEFAULT_PRESET) or theme.DEFAULT_PRESET)
        self._theme_mode = str(s.value("theme/mode", "dark") or "dark")
        self._theme_seed = str(s.value("theme/seed", "") or "") or None
        self.scanner: ScanWorker | None = None
        self._rescan_pending = False
        self.view: dict = dict(SONGS)
        self._queue_source: dict = dict(SONGS)
        self._unmute_volume = 70

        self._build_ui()
        self._build_menu()
        self.media = system_media.create(self)
        self._connect()
        self._restore()
        self._refresh_nav()
        self._reload()
        self._restore_session()
        self._rescan()

    # ---- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 4, 16, 0)
        body.setSpacing(12)
        body.addWidget(self._build_drawer())
        body.addWidget(self._build_content(), 1)
        body.addWidget(self._build_now_playing())
        outer.addLayout(body, 1)
        outer.addWidget(self._build_bar())

    def _build_drawer(self) -> QWidget:
        drawer = QWidget(objectName="drawer")
        drawer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        drawer.setFixedWidth(264)
        lay = QVBoxLayout(drawer)
        lay.setContentsMargins(0, 8, 0, 12)
        lay.setSpacing(4)

        self.nav = QListWidget(objectName="nav")
        self.nav.setIconSize(QSize(24, 24))
        self.nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_new_pl = m3_button("New playlist", "tonal", "add")
        self.btn_add_folder = m3_button("Add music folder", "", "create_new_folder")
        self.btn_theme = icon_button("palette", "Theme and appearance (Ctrl+T)")
        brand = QHBoxLayout()
        brand.setContentsMargins(0, 0, 16, 0)
        brand.addWidget(QLabel("Music", objectName="brand"), 1)
        brand.addWidget(self.btn_theme, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(brand)
        lay.addWidget(self.nav, 1)
        for b in (self.btn_new_pl, self.btn_add_folder):
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 16, 0)
            row.addWidget(b)
            lay.addLayout(row)
        return drawer

    def _build_content(self) -> QWidget:
        pane = QWidget(objectName="pane")
        pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(24, 20, 24, 8)
        lay.setSpacing(14)

        self.btn_back = icon_button("arrow_back", "Back to albums", role="on_surface")
        self.btn_back.hide()
        self.view_title = QLabel("Songs", objectName="headline")
        self.view_sub = QLabel("", objectName="muted")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.view_title)
        titles.addWidget(self.view_sub)

        self.search = QLineEdit(objectName="search", placeholderText="Search songs, artists and albums")
        self.search.setClearButtonEnabled(True)
        theme.bind(self.search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition), "search")
        self.search.setFixedHeight(48)
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(340)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.btn_back, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(titles, 1)
        head.addWidget(self.search, 0, Qt.AlignmentFlag.AlignTop)

        self.btn_play_all = m3_button("Play", "filled", "play_arrow")
        self.btn_shuffle_all = m3_button("Shuffle", "tonal", "shuffle")
        self.actions_w = QWidget()
        actions = QHBoxLayout(self.actions_w)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.btn_play_all)
        actions.addWidget(self.btn_shuffle_all)
        actions.addStretch(1)

        # tracks table
        t = self.table = QTableView()
        t.setModel(self.tracks)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(False)
        t.setWordWrap(False)
        t.setCornerButtonEnabled(False)
        t.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(44)
        hh = t.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_RATING, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        hh.setMinimumSectionSize(56)
        hh.resizeSection(COL_RATING, 104)
        self.star_delegate = StarDelegate(t)
        t.setItemDelegateForColumn(COL_RATING, self.star_delegate)

        # album grid
        g = self.grid = QListView(objectName="grid")
        g.setModel(self.albums)
        g.setItemDelegate(AlbumDelegate(self.covers, g))
        g.setViewMode(QListView.ViewMode.IconMode)
        g.setResizeMode(QListView.ResizeMode.Adjust)
        g.setMovement(QListView.Movement.Static)
        g.setUniformItemSizes(True)
        g.setWrapping(True)
        g.setSpacing(6)
        g.setDragEnabled(False)
        g.setMouseTracking(True)
        g.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        g.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        g.verticalScrollBar().setSingleStep(32)
        g.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        g.setCursor(Qt.CursorShape.PointingHandCursor)

        self.empty = QLabel("", objectName="empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)

        self.stats_page = StatsPage(self.library, self.covers)
        self.stack = QStackedWidget()
        for w in (self.table, self.grid, self.empty, self.stats_page):
            self.stack.addWidget(w)

        lay.addLayout(head)
        lay.addWidget(self.actions_w)
        lay.addWidget(self.stack, 1)
        return pane

    def _build_now_playing(self) -> QWidget:
        pane = QWidget(objectName="pane")
        pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pane.setFixedWidth(COVER + 48)
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # segmented tabs
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.np_tabs: list[QPushButton] = []
        for i, label in enumerate(("Playing", "Lyrics", "Up Next")):
            b = QPushButton(label, objectName="segment")
            b.setCheckable(True)
            b.setProperty("seg", "first" if i == 0 else "last" if i == 2 else "mid")
            b.setProperty("small", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=i: self._set_np_tab(k))
            self.np_tabs.append(b)
            tabs.addWidget(b, 1)
        lay.addLayout(tabs)

        # compact "now playing" header shown above lyrics / queue
        self.mini = QWidget()
        ml = QHBoxLayout(self.mini)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        self.mini_cover = QLabel()
        self.mini_cover.setFixedSize(48, 48)
        mt = QVBoxLayout()
        mt.setSpacing(0)
        self.mini_title = QLabel("Nothing playing", objectName="miniTitle")
        self.mini_artist = QLabel("", objectName="muted")
        mt.addWidget(self.mini_title)
        mt.addWidget(self.mini_artist)
        ml.addWidget(self.mini_cover)
        ml.addLayout(mt, 1)
        lay.addWidget(self.mini)

        # page 0: big cover
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(8, 4, 8, 0)
        pl.setSpacing(4)
        self.cover = QLabel()
        self.cover.setFixedSize(COVER, COVER)
        self.title_lbl = QLabel("Nothing playing", objectName="title")
        self.title_lbl.setWordWrap(True)
        self.artist_lbl = QLabel("Pick a song to start listening", objectName="artist")
        self.artist_lbl.setWordWrap(True)
        self.album_lbl = QLabel("", objectName="muted")
        self.album_lbl.setWordWrap(True)
        self.album_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.album_lbl.setToolTip("Go to album")
        self.stars = StarBar()
        self.stars.setEnabled(False)
        self.plays_lbl = QLabel("", objectName="muted")
        pl.addWidget(self.cover)
        pl.addSpacing(16)
        for w in (self.title_lbl, self.artist_lbl, self.album_lbl):
            pl.addWidget(w)
        pl.addSpacing(8)
        pl.addWidget(self.stars)
        pl.addWidget(self.plays_lbl)
        pl.addStretch(1)

        self.lyrics = LyricsPanel(self.library, self.settings, self)
        self.queue_panel = QueuePanel(self.engine, self.covers)
        self.np_stack = QStackedWidget()
        for w in (page, self.lyrics, self.queue_panel):
            self.np_stack.addWidget(w)
        lay.addWidget(self.np_stack, 1)
        self._set_cover(None)
        return pane

    def _set_np_tab(self, k: int) -> None:
        self.np_stack.setCurrentIndex(k)
        for i, b in enumerate(self.np_tabs):
            b.setChecked(i == k)
        self.mini.setVisible(k != 0)
        self.lyrics.set_active(k == 1)
        self.settings.setValue("ui/np_tab", k)

    def _build_bar(self) -> QWidget:
        bar = QWidget(objectName="bar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(24, 6, 24, 10)
        lay.setSpacing(2)

        self.pos_lbl = QLabel("0:00", objectName="time")
        self.dur_lbl = QLabel("0:00", objectName="time")
        self.pos_lbl.setMinimumWidth(44)
        self.dur_lbl.setMinimumWidth(44)
        self.pos_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.seek = JumpSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(12)
        seek_row.addWidget(self.pos_lbl)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.dur_lbl)

        self.btn_shuffle = icon_button("shuffle", "Shuffle (Ctrl+S)")
        self.btn_prev = icon_button("skip_previous", "Previous (Ctrl+Left)", 48, 28, "on_surface")
        self.btn_play = icon_button("play_arrow", "Play / Pause (Ctrl+Space)", 64, 32, "on_primary")
        self.btn_play.setObjectName("fab")
        self.btn_next = icon_button("skip_next", "Next (Ctrl+Right)", 48, 28, "on_surface")
        self.btn_repeat = icon_button("repeat", "Repeat: off (Ctrl+R)")
        self.btn_eq = icon_button("equalizer", "Equalizer (Ctrl+E)")
        self.btn_vol = icon_button("volume_up", "Mute")
        self.vol = JumpSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(120)

        self.btn_output = QToolButton(objectName="chip")
        self.btn_output.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_output.setIconSize(QSize(18, 18))
        self.btn_output.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_output.setCursor(Qt.CursorShape.PointingHandCursor)
        self.output_menu = QMenu(self)
        self.output_menu.aboutToShow.connect(lambda: self._fill_output_menu(self.output_menu))
        self.btn_output.setMenu(self.output_menu)

        left = QWidget()
        left.setFixedWidth(230)
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.btn_output)
        ll.addStretch(1)
        right = QWidget()
        right.setFixedWidth(230)
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addStretch(1)
        rl.addWidget(self.btn_eq)
        rl.addWidget(self.btn_vol)
        rl.addWidget(self.vol)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(12)
        ctrl.addWidget(left)
        ctrl.addStretch(1)
        for w in (self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat):
            ctrl.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        ctrl.addStretch(1)
        ctrl.addWidget(right)

        lay.addLayout(seek_row)
        lay.addLayout(ctrl)
        return bar

    def _build_menu(self) -> None:
        def menu_action(menu, text, slot, key=None, checkable=False):
            act = QAction(text, self)
            if key:
                act.setShortcut(QKeySequence(key))
            act.setCheckable(checkable)
            if checkable:
                act.toggled.connect(slot)
            else:
                act.triggered.connect(lambda _c=False, s=slot: s())
            menu.addAction(act)
            return act

        mb = self.menuBar()
        m = mb.addMenu("&File")
        menu_action(m, "&Add Music Folder…", self._pick_folder, "Ctrl+O")
        menu_action(m, "&Remove Folder…", self._remove_folder)
        menu_action(m, "Re&scan Library", self._rescan, "F5")
        m.addSeparator()
        menu_action(m, "&Import Playlist (M3U)…", self._import_playlist)
        m.addSeparator()
        menu_action(m, "&Quit", self.close, "Ctrl+Q")

        m = mb.addMenu("&View")
        menu_action(m, "&Songs", lambda: self._navigate(SONGS), "Ctrl+1")
        menu_action(m, "&Albums", lambda: self._navigate(ALBUMS), "Ctrl+2")
        menu_action(m, "&Find", lambda: (self.search.setFocus(), self.search.selectAll()), "Ctrl+F")
        m.addSeparator()
        menu_action(m, "Now &Playing", lambda: self._set_np_tab(0), "Ctrl+P")
        menu_action(m, "&Lyrics", lambda: self._set_np_tab(1), "Ctrl+L")
        menu_action(m, "&Up Next", lambda: self._set_np_tab(2), "Ctrl+U")
        menu_action(m, "Your &Year in Music", lambda: self._navigate(STATS), "Ctrl+Y")
        m.addSeparator()
        theme_menu = m.addMenu("&Theme")
        theme.bind(theme_menu.menuAction(), "palette")
        theme_menu.aboutToShow.connect(lambda: fill_theme_menu(theme_menu, self))
        menu_action(m, "A&ppearance…", self.show_theme_dialog, "Ctrl+T")

        m = mb.addMenu("&Playback")
        menu_action(m, "&Play / Pause", self.engine.toggle, "Ctrl+Space")
        menu_action(m, "&Next", self.engine.next, "Ctrl+Right")
        menu_action(m, "P&revious", self.engine.previous, "Ctrl+Left")
        m.addSeparator()
        self.act_shuffle = menu_action(m, "S&huffle", self._set_shuffle, "Ctrl+S", checkable=True)
        menu_action(m, "Cycle &Repeat", self._cycle_repeat, "Ctrl+R")
        m.addSeparator()
        menu_action(m, "&Equalizer…", self.show_equalizer, "Ctrl+E")
        m.addSeparator()
        self.act_hotkeys = menu_action(m, "&Global Hotkeys", self._set_hotkeys, checkable=True)
        m.addSeparator()
        out_menu = m.addMenu("&Output Device")
        theme.bind(out_menu.menuAction(), "speaker")
        out_menu.aboutToShow.connect(lambda: self._fill_output_menu(out_menu))

        m = mb.addMenu("Play&list")
        menu_action(m, "&New Playlist…", self._new_playlist, "Ctrl+N")
        menu_action(m, "&Import M3U…", self._import_playlist)

        # Delete removes from the open playlist.
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._remove_selected_from_playlist)

    def _connect(self) -> None:
        e = self.engine
        e.positionChanged.connect(self._on_position)
        e.durationChanged.connect(self._on_duration)
        e.playingChanged.connect(self._on_playing)
        e.trackChanged.connect(self._on_track)
        e.errorOccurred.connect(lambda msg: self.statusBar().showMessage(msg, 8000))
        e.stateChanged.connect(self.media.update_state)

        self.tracker = PlayTracker(e, self.library, self)
        self.tracker.recorded.connect(self._on_play_recorded)
        e.positionChanged.connect(lambda ms: self.lyrics.view.set_position(ms, e.is_playing))
        e.playingChanged.connect(lambda playing: self.lyrics.view.set_position(e.position, playing))
        self.lyrics.seekRequested.connect(e.seek)
        self.star_delegate.rated.connect(self._rate_row)
        self.stars.rated.connect(self._rate_current)
        self.stats_page.playPaths.connect(self._play_paths)
        self.stats_page.openAlbum.connect(self._open_album)
        self.devices.devicesChanged.connect(self._apply_output)
        self.devices.deviceConnected.connect(self._on_device_connected)
        self.devices.deviceDisconnected.connect(self._on_device_disconnected)

        self.media.command.connect(self._on_media_command)
        self.media.message.connect(lambda msg: self.statusBar().showMessage(msg, 10000))
        if not self.media.hotkeys_supported:
            self.act_hotkeys.setEnabled(False)
            self.act_hotkeys.setText("Global Hotkeys (Windows only)")
        if sys.platform != "win32":
            # Windows routes media keys through the system overlay instead.
            for key, fn in ((Qt.Key.Key_MediaTogglePlayPause, e.toggle), (Qt.Key.Key_MediaPlay, e.toggle),
                            (Qt.Key.Key_MediaNext, e.next), (Qt.Key.Key_MediaPrevious, e.previous)):
                QShortcut(QKeySequence(key), self).activated.connect(fn)

        self.btn_play.clicked.connect(e.toggle)
        self.btn_next.clicked.connect(lambda: e.next())
        self.btn_prev.clicked.connect(e.previous)
        self.btn_shuffle.clicked.connect(lambda: self._set_shuffle(not e.shuffle))
        self.btn_repeat.clicked.connect(self._cycle_repeat)
        self.btn_vol.clicked.connect(self._toggle_mute)

        self.seek.valueChanged.connect(lambda v: self.pos_lbl.setText(fmt(v)))
        self.seek.seekRequested.connect(e.seek)
        self.vol.valueChanged.connect(self._on_volume)

        self.nav.currentItemChanged.connect(self._on_nav)
        self.nav.itemClicked.connect(self._on_nav_clicked)
        self.nav.customContextMenuRequested.connect(self._nav_menu)
        self.btn_new_pl.clicked.connect(lambda: self._new_playlist())
        self.btn_add_folder.clicked.connect(self._pick_folder)
        self.btn_theme.clicked.connect(self.show_theme_dialog)
        self.btn_eq.clicked.connect(self.show_equalizer)
        QApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme)
        self.btn_back.clicked.connect(lambda: self._navigate(ALBUMS))
        self.btn_play_all.clicked.connect(lambda: self._play_all(shuffle=False))
        self.btn_shuffle_all.clicked.connect(lambda: self._play_all(shuffle=True))
        self.album_lbl.linkActivated.connect(lambda _href: self._goto_current_album())

        self.table.activated.connect(lambda idx: self._play_rows(self.tracks.tracks(), idx.row()))
        self.table.customContextMenuRequested.connect(self._track_menu)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        self.grid.activated.connect(self._open_album_index)
        self.grid.customContextMenuRequested.connect(self._album_menu)
        self.covers.changed.connect(self.grid.viewport().update)

        self._search_timer = QTimer(self, singleShot=True, interval=200)
        self._search_timer.timeout.connect(self._reload)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())

        self._session_timer = QTimer(self, interval=10_000)
        self._session_timer.timeout.connect(lambda: self.engine.is_playing and self._save_session())
        self._session_timer.start()

    # ---- settings / session ------------------------------------------------
    def _folders(self) -> list[str]:
        v = self.settings.value("folders", [])
        if isinstance(v, str):
            v = [v]
        return [str(p) for p in (v or [])]

    def _restore(self) -> None:
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.vol.setValue(int(self.settings.value("volume", 70)))
        self._on_volume(self.vol.value())
        self.engine.set_shuffle(self.settings.value("shuffle", False, type=bool))
        self.engine.set_repeat(RepeatMode(int(self.settings.value("repeat", 0))))
        self._refresh_modes()

        col = int(self.settings.value("sort/column", 0))
        order = Qt.SortOrder(int(self.settings.value("sort/order", 0)))
        self.table.horizontalHeader().setSortIndicator(col if 0 <= col <= COL_TIME else 0, order)
        self.table.setSortingEnabled(True)

        if self.media.hotkeys_supported:
            self.act_hotkeys.setChecked(self.settings.value("hotkeys", True, type=bool))

        self._pinned_id = str(self.settings.value("audio/device", "") or "")
        self._pinned_name = str(self.settings.value("audio/device_name", "") or "")
        self._auto_switch = self.settings.value("audio/auto_switch", True, type=bool)
        self._pause_on_disconnect = self.settings.value("audio/pause_on_disconnect", True, type=bool)
        self._set_np_tab(int(self.settings.value("ui/np_tab", 0) or 0) % 3)
        self._apply_output()
        self.engine.set_normalize(self.eq_store.normalize)
        self._load_eq_for_device(force=True)
        if self.engine.fallback_reason:
            self.statusBar().showMessage("Basic audio engine in use, so the equalizer is off. "
                                         "Open Playback > Equalizer for details.", 10000)

    def _source_for(self, view: dict, search: str | None = None) -> dict:
        col, order = self.tracks.sort_state()
        src = dict(view)
        src["search"] = self.search.text() if search is None else search
        src["sort"] = [col, order.value]
        return src

    def _save_session(self) -> None:
        t = self.engine.current_track
        if t is None:
            return
        s = self.settings
        s.setValue("session/path", t["path"])
        s.setValue("session/position", int(self.engine.position))
        s.setValue("session/source", json.dumps(self._queue_source))

    def _restore_session(self) -> None:
        """Reload last session's queue and track, paused at the saved position."""
        path = self.settings.value("session/path", "")
        if not path:
            return
        try:
            src = json.loads(self.settings.value("session/source", "") or "{}")
        except (TypeError, ValueError):
            src = {}
        if src.get("kind") not in ("songs", "album", "playlist", "smart"):
            src = dict(SONGS)
        try:
            col, order = (int(x) for x in src.get("sort", [0, 0]))
            order = Qt.SortOrder(order)
        except (TypeError, ValueError):
            col, order = 0, Qt.SortOrder.AscendingOrder
        rows = sort_rows(self._rows_for(src, src.get("search", "")), col, order)
        idx = next((i for i, t in enumerate(rows) if t["path"] == path), -1)
        if idx < 0:                      # source gone (playlist deleted, etc.): fall back to library
            src = self._source_for(SONGS, "")
            rows = self.library.search("")
            idx = next((i for i, t in enumerate(rows) if t["path"] == path), -1)
        if idx < 0:
            return
        self._queue_source = src
        pos = int(self.settings.value("session/position", 0) or 0)
        self.engine.set_queue(rows, start=idx, autoplay=False, position_ms=pos)
        self.statusBar().showMessage(f"Ready to resume “{rows[idx]['title']}” at {fmt(pos)}", 6000)

    def closeEvent(self, event) -> None:
        self._save_session()                 # before stop() resets the position
        self.tracker.finish(closing=True)
        self.lyrics.shutdown()
        if self.scanner is not None and self.scanner.isRunning():
            self.scanner.requestInterruption()
            self.scanner.wait(5000)
        self.covers.shutdown()
        self.media.shutdown()
        self.save_eq()
        self.engine.shutdown()
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("volume", self.vol.value())
        s.setValue("shuffle", self.engine.shuffle)
        s.setValue("repeat", self.engine.repeat.value)
        s.setValue("hotkeys", self.act_hotkeys.isChecked())
        s.setValue("audio/auto_switch", self._auto_switch)
        s.setValue("audio/pause_on_disconnect", self._pause_on_disconnect)
        super().closeEvent(event)

    # ---- navigation ---------------------------------------------------------
    @staticmethod
    def _nav_key(view: dict) -> tuple:
        kind = "albums" if view["kind"] == "album" else view["kind"]
        return kind, view.get("id")

    def _refresh_nav(self) -> None:
        want = self._nav_key(self.view)
        self.nav.blockSignals(True)
        self.nav.clear()

        def add(text, icon_name, view, tip=""):
            it = QListWidgetItem(icon(icon_name, C["on_surface_variant"], C["on_secondary_container"]), text)
            it.setData(Qt.ItemDataRole.UserRole, view)
            it.setSizeHint(QSize(220, 56))
            if tip:
                it.setToolTip(tip)
            self.nav.addItem(it)
            if self._nav_key(view) == want:
                self.nav.setCurrentItem(it)

        def section(text):
            header = QListWidgetItem(text)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setSizeHint(QSize(220, 44))
            f = QFont(self.font())
            f.setWeight(QFont.Weight.DemiBold)
            header.setFont(f)
            self.nav.addItem(header)

        add("Songs", "library_music", SONGS)
        add("Albums", "album", ALBUMS)
        add("Your year in music", "insights", STATS)
        section("Smart playlists")
        for key, (name, *_rest) in SMART.items():
            add(name, SMART_ICONS.get(key, "auto_awesome"), {"kind": "smart", "id": key, "name": name},
                "Updates automatically from your listening")
        section("Playlists")
        playlists = self.library.playlists()
        for pl in playlists:
            add(pl["name"], "queue_music", {"kind": "playlist", "id": pl["id"], "name": pl["name"]},
                f"{pl['tracks']} songs")
        if not playlists:
            hint = QListWidgetItem("Right-click songs to add them")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setSizeHint(QSize(220, 36))
            self.nav.addItem(hint)
        self.nav.blockSignals(False)

    def _on_nav(self, current: QListWidgetItem | None, _previous=None) -> None:
        view = current.data(Qt.ItemDataRole.UserRole) if current else None
        if view:
            self.view = dict(view)
            self._reload()

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        # Clicking "Albums" while inside an album goes back to the grid.
        view = item.data(Qt.ItemDataRole.UserRole)
        if view and view["kind"] == "albums" and self.view["kind"] == "album":
            self._navigate(ALBUMS)

    def _navigate(self, view: dict) -> None:
        self.view = dict(view)
        self._refresh_nav()
        self._reload()

    def _open_album(self, album: str, artist: str) -> None:
        self._navigate({"kind": "album", "album": album, "artist": artist})

    def _open_album_index(self, index: QModelIndex) -> None:
        a = index.data(Qt.ItemDataRole.UserRole)
        if a:
            self._open_album(a["album"], a["artist"])

    def _goto_current_album(self) -> None:
        t = self.engine.current_track
        if t:
            self._open_album(t["album"], t.get("album_artist") or t["artist"])

    # ---- library views ------------------------------------------------------
    def _rows_for(self, view: dict, text: str) -> list[dict]:
        kind = view["kind"]
        if kind == "album":
            return self.library.album_tracks(view["album"], view["artist"], text)
        if kind == "playlist":
            return self.library.playlist_tracks(view["id"], text)
        if kind == "smart" and view.get("id") in SMART:
            return self.library.smart_playlist(view["id"], text)
        return self.library.search(text)

    def _reload(self) -> None:
        text = self.search.text()
        view, kind = self.view, self.view["kind"]
        self.btn_back.setVisible(kind == "album")
        if kind == "stats":
            self.view_title.setText("Your year in music")
            self.view_sub.setText("Counted from what you actually listen to")
            self.actions_w.hide()
            self.stats_page.refresh()
            self._show(self.stats_page)
            return
        if kind == "albums":
            albums = self.library.albums(text)
            self.albums.set_albums(albums)
            self.view_title.setText("Albums")
            self.view_sub.setText(f"{len(albums)} albums")
            self.actions_w.hide()
            self._show(self.grid if albums else None, "No albums match your search." if text
                       else "Add a music folder to see your albums here.")
            return

        rows = self._rows_for(view, text)
        self.tracks.set_rows(rows, "track" if kind == "album" else "row")
        total = sum(t["duration_ms"] for t in rows)
        summary = f"{len(rows)} songs, {fmt_long(total)}" if rows else "No songs"
        if kind == "album":
            self.view_title.setText(view["album"])
            self.view_sub.setText(f"{view['artist']}  •  {summary}")
        elif kind in ("playlist", "smart"):
            self.view_title.setText(view["name"])
            self.view_sub.setText(summary + ("  •  updates automatically" if kind == "smart" else ""))
        else:
            self.view_title.setText("Songs")
            self.view_sub.setText(summary)
        self.actions_w.show()
        for b in (self.btn_play_all, self.btn_shuffle_all):
            b.setEnabled(bool(rows))
        if rows:
            self._show(self.table)
        elif text:
            self._show(None, "No songs match your search.")
        elif kind == "playlist":
            self._show(None, "This playlist is empty.\nRight-click any song and choose Add to playlist.")
        elif kind == "smart":
            self._show(None, {"most_played": "Nothing played yet. Songs appear here as you listen.",
                              "recently_played": "Nothing played yet.",
                              "top_rated": "No songs rated 4 or 5 stars yet.\nClick the stars in the Rating column.",
                              "forgotten": "No forgotten favourites: songs you played 3+ times\n"
                                           "but not in the last 2 months show up here.",
                              }.get(view["id"], "No songs here yet."))
        else:
            self._show(None, "Your library is empty.\nAdd a music folder (Ctrl+O) or drop one on this window.")

    def _show(self, widget: QWidget | None, empty_text: str = "") -> None:
        if widget is None:
            self.empty.setText(empty_text)
            widget = self.empty
        self.stack.setCurrentWidget(widget)

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self.settings.setValue("sort/column", column)
        self.settings.setValue("sort/order", order.value)

    # ---- folders / scanning ---------------------------------------------------
    def _pick_folder(self) -> None:
        start = (self._folders() or [str(Path.home() / "Music")])[-1]
        path = QFileDialog.getExistingDirectory(self, "Add music folder", start)
        if path:
            self._add_folder(path)

    def _add_folder(self, path: str) -> None:
        path = os.path.normpath(path)
        folders = self._folders()
        if path not in folders:
            folders.append(path)
            self.settings.setValue("folders", folders)
        self._rescan()

    def _remove_folder(self) -> None:
        folders = self._folders()
        if not folders:
            self.statusBar().showMessage("No folders in the library yet.", 4000)
            return
        path, ok = QInputDialog.getItem(self, "Remove folder", "Folder to remove:", folders, 0, False)
        if ok and path:
            folders.remove(path)
            self.settings.setValue("folders", folders)
            self.library.remove_under(path)
            self.covers.clear()
            self._refresh_nav()
            self._reload()

    def _rescan(self) -> None:
        folders = self._folders()
        if not folders:
            self.statusBar().showMessage("Add a music folder with File > Add Music Folder (Ctrl+O).")
            return
        if self.scanner is not None and self.scanner.isRunning():
            self._rescan_pending = True
            return
        self.scanner = ScanWorker(self.library, folders, self)
        self.scanner.progress.connect(lambda n: self.statusBar().showMessage(f"Scanning… {n} files"))
        self.scanner.finished.connect(self._scan_finished)
        self.scanner.start()

    def _scan_finished(self) -> None:
        w = self.scanner
        if w is not None:
            self.statusBar().showMessage(f"Library updated: {w.changed} new or changed, {w.removed} removed", 6000)
            if w.changed or w.removed:
                self.covers.clear()
                self._refresh_nav()
                self._reload()
        if self._rescan_pending:
            self._rescan_pending = False
            self._rescan()

    # ---- playlists ------------------------------------------------------------
    def _selected_tracks(self) -> list[dict]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.tracks.track(r) for r in rows]

    def _ask_name(self, title: str, default: str) -> str:
        name, ok = QInputDialog.getText(self, title, "Name", QLineEdit.EchoMode.Normal, default)
        return name.strip() if ok else ""

    def _new_playlist(self, paths: list[str] | None = None) -> None:
        existing = {p["name"] for p in self.library.playlists()}
        default, n = "New playlist", 2
        while default in existing:
            default, n = f"New playlist {n}", n + 1
        name = self._ask_name("New playlist", default)
        if not name:
            return
        pid = self.library.create_playlist(name)
        if paths:
            added = self.library.add_to_playlist(pid, paths)
            self.statusBar().showMessage(f"Created “{name}” with {added} songs", 5000)
            self._refresh_nav()
        else:
            self._navigate({"kind": "playlist", "id": pid, "name": name})

    def _add_to_playlist(self, pid: int, name: str, paths: list[str]) -> None:
        added = self.library.add_to_playlist(pid, paths)
        skipped = len(paths) - added
        msg = f"Added {added} song{'s' if added != 1 else ''} to “{name}”"
        if skipped:
            msg += f" ({skipped} already there)"
        self.statusBar().showMessage(msg, 5000)
        self._refresh_nav()
        if self.view.get("kind") == "playlist" and self.view.get("id") == pid:
            self._reload()

    def _remove_selected_from_playlist(self) -> None:
        if self.view["kind"] != "playlist":
            return
        sel = self._selected_tracks()
        if sel:
            self.library.remove_entries(self.view["id"], [t["entry_id"] for t in sel])
            self._refresh_nav()
            self._reload()

    def _move_entry(self, track: dict, delta: int) -> None:
        if self.library.move_entry(self.view["id"], track["entry_id"], delta):
            self._reload()
            for row, t in enumerate(self.tracks.tracks()):
                if t["entry_id"] == track["entry_id"]:
                    self.table.selectRow(row)
                    break

    def _rename_playlist(self, view: dict) -> None:
        name = self._ask_name("Rename playlist", view["name"])
        if name and name != view["name"]:
            self.library.rename_playlist(view["id"], name)
            if self.view.get("kind") == "playlist" and self.view.get("id") == view["id"]:
                self.view["name"] = name
            if self._queue_source.get("id") == view["id"]:
                self._queue_source["name"] = name
            self._refresh_nav()
            self._reload()

    def _delete_playlist(self, view: dict) -> None:
        answer = QMessageBox.question(self, "Delete playlist",
                                      f"Delete “{view['name']}”? The songs stay in your library.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_playlist(view["id"])
        if self.view.get("kind") == "playlist" and self.view.get("id") == view["id"]:
            self.view = dict(SONGS)
        self._refresh_nav()
        self._reload()

    def _export_playlist(self, view: dict) -> None:
        suggested = str(Path.home() / f"{view['name']}.m3u8")
        path, _ = QFileDialog.getSaveFileName(self, "Export playlist", suggested, "M3U playlist (*.m3u8 *.m3u)")
        if path:
            rows = self.library.playlist_tracks(view["id"])
            write_m3u(path, rows)
            self.statusBar().showMessage(f"Exported {len(rows)} songs to {path}", 6000)

    def _import_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import playlist", str(Path.home()),
                                              "M3U playlist (*.m3u8 *.m3u)")
        if not path:
            return
        try:
            paths = read_m3u(path)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not read {path}: {exc}", 8000)
            return
        name = Path(path).stem
        pid = self.library.create_playlist(name)
        self.library.add_to_playlist(pid, paths)
        found = len(self.library.playlist_tracks(pid))
        self._navigate({"kind": "playlist", "id": pid, "name": name})
        missing = len(paths) - found
        self.statusBar().showMessage(
            f"Imported “{name}”: {found} songs" + (f", {missing} not in your library yet" if missing else ""), 8000)

    def _playlist_submenu(self, menu: QMenu, paths: list[str]) -> None:
        sub = menu.addMenu(icon("playlist_add"), "Add to playlist")
        add_action(sub, "add", "New playlist…", lambda: self._new_playlist(paths))
        playlists = self.library.playlists()
        if playlists:
            sub.addSeparator()
        for pl in playlists:
            add_action(sub, "queue_music", pl["name"],
                       lambda pid=pl["id"], name=pl["name"]: self._add_to_playlist(pid, name, paths))

    # ---- context menus ----------------------------------------------------------
    def _track_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        if not self.table.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            self.table.selectRow(idx.row())
        sel = self._selected_tracks()
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_rows(self.tracks.tracks(), idx.row()))
        self._queue_actions(m, sel)
        m.addSeparator()
        self._playlist_submenu(m, [t["path"] for t in sel])
        self._rate_submenu(m, sel)
        if self.view["kind"] == "playlist":
            can_move = len(sel) == 1 and self.tracks.natural_order() and not self.search.text()
            m.addSeparator()
            add_action(m, "arrow_upward", "Move up", lambda: self._move_entry(sel[0], -1), can_move)
            add_action(m, "arrow_downward", "Move down", lambda: self._move_entry(sel[0], 1), can_move)
            add_action(m, "remove", "Remove from playlist", self._remove_selected_from_playlist)
        if len(sel) == 1:
            t = sel[0]
            m.addSeparator()
            if self.view["kind"] != "album":
                add_action(m, "album", "Go to album",
                           lambda: self._open_album(t["album"], t.get("album_artist") or t["artist"]))
            add_action(m, "folder", "Show in folder", lambda: self._reveal(t["path"]))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _album_menu(self, pos) -> None:
        idx = self.grid.indexAt(pos)
        a = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if not a:
            return
        view = {"kind": "album", "album": a["album"], "artist": a["artist"]}
        rows = self.library.album_tracks(a["album"], a["artist"])
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_source(view, rows, False))
        add_action(m, "shuffle", "Shuffle", lambda: self._play_source(view, rows, True))
        add_action(m, "album", "Open", lambda: self._open_album(a["album"], a["artist"]))
        self._queue_actions(m, rows)
        m.addSeparator()
        self._playlist_submenu(m, [t["path"] for t in rows])
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _nav_menu(self, pos) -> None:
        item = self.nav.itemAt(pos)
        view = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not view or view["kind"] not in ("playlist", "smart"):
            return
        rows = self._rows_for(view, "")
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_source(view, rows, False), bool(rows))
        add_action(m, "shuffle", "Shuffle", lambda: self._play_source(view, rows, True), bool(rows))
        self._queue_actions(m, rows)
        if view["kind"] == "smart":
            m.exec(self.nav.viewport().mapToGlobal(pos))
            return
        m.addSeparator()
        add_action(m, "edit", "Rename…", lambda: self._rename_playlist(view))
        add_action(m, "file_upload", "Export as M3U…", lambda: self._export_playlist(view), bool(rows))
        m.addSeparator()
        add_action(m, "delete", "Delete", lambda: self._delete_playlist(view))
        m.exec(self.nav.viewport().mapToGlobal(pos))

    def _queue_actions(self, menu: QMenu, tracks: list[dict]) -> None:
        n = len(tracks)
        add_action(menu, "skip_next", "Play next", lambda: self._enqueue(tracks, True), n > 0)
        add_action(menu, "queue_music", "Add to queue", lambda: self._enqueue(tracks, False), n > 0)

    def _enqueue(self, tracks: list[dict], play_next: bool) -> None:
        self.engine.enqueue(tracks, play_next)
        what = f"“{tracks[0]['title']}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        self.statusBar().showMessage(f"{what} will play next" if play_next else f"Added {what} to the queue", 4000)

    def _rate_submenu(self, menu: QMenu, tracks: list[dict]) -> None:
        sub = menu.addMenu(icon("star"), "Rate")
        current = tracks[0].get("rating", 0) if len(tracks) == 1 else -1
        for r in range(5, -1, -1):
            act = sub.addAction("★" * r + "☆" * (5 - r) if r else "No rating")
            act.setCheckable(True)
            act.setChecked(r == current)
            act.triggered.connect(lambda _c=False, r=r: self._rate_tracks(tracks, r))

    def _rate_tracks(self, tracks: list[dict], rating: int) -> None:
        for t in tracks:
            self.library.set_rating(t["path"], rating)
            t["rating"] = rating
        self.tracks.layoutChanged.emit()
        self._refresh_now_playing_stats()

    def _rate_row(self, row: int, rating: int) -> None:
        t = self.tracks.track(row)
        self.library.set_rating(t["path"], rating)
        self.tracks.set_rating(row, rating)
        cur = self.engine.current_track
        if cur and cur["path"] == t["path"]:
            cur["rating"] = rating
            self._refresh_now_playing_stats()

    def _rate_current(self, rating: int) -> None:
        cur = self.engine.current_track
        if cur:
            self._rate_tracks([cur], rating)
            row = self.tracks.row_of(cur["id"])
            if row >= 0:
                self.tracks.set_rating(row, rating)

    def _refresh_now_playing_stats(self) -> None:
        cur = self.engine.current_track
        self.stars.setEnabled(cur is not None)
        self.stars.set_rating(cur.get("rating", 0) if cur else 0)
        if cur:
            n = cur.get("play_count", 0)
            self.plays_lbl.setText(f"Played {n} time{'s' if n != 1 else ''}" if n else "Not played before")
        else:
            self.plays_lbl.setText("")

    def _on_play_recorded(self, path: str, counted: bool) -> None:
        self._refresh_now_playing_stats()
        if self.view["kind"] == "stats":
            self.stats_page.refresh()

    def _play_paths(self, paths: list[str], start: int) -> None:
        rows = self.library.tracks_by_paths(paths)
        if rows:
            start_path = paths[start] if 0 <= start < len(paths) else None
            idx = next((i for i, t in enumerate(rows) if t["path"] == start_path), 0)
            self._play_source(SONGS, rows, False, idx)

    @staticmethod
    def _reveal(path: str) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ---- playback / UI sync --------------------------------------------------
    def _play_rows(self, rows: list[dict], start: int, shuffle: bool | None = None) -> None:
        self._play_source(self.view, rows, shuffle, start)

    def _play_source(self, view: dict, rows: list[dict], shuffle: bool | None, start: int | None = None) -> None:
        """Play `rows`; `view` is remembered so the queue can be rebuilt on next launch."""
        if not rows:
            return
        if shuffle is not None and shuffle != self.engine.shuffle:
            self._set_shuffle(shuffle)
        if start is None:
            start = random.randrange(len(rows)) if shuffle else 0
        same_view = view == self.view
        self._queue_source = self._source_for(view, None if same_view else "")
        if not same_view:
            # Rows came straight from the library (natural order), so record that.
            self._queue_source["sort"] = [0, Qt.SortOrder.AscendingOrder.value]
        self.engine.set_queue(rows, start)

    def _play_all(self, shuffle: bool) -> None:
        self._play_rows(self.tracks.tracks(), random.randrange(max(1, len(self.tracks.tracks())))
                        if shuffle else 0, shuffle)

    def _on_position(self, ms: int) -> None:
        if not self.seek.dragging:
            self.seek.setValue(ms)

    def _on_duration(self, ms: int) -> None:
        self.seek.setRange(0, max(0, ms))
        self.dur_lbl.setText(fmt(ms))
        if not self.seek.dragging:
            self.seek.setValue(self.engine.position)

    def _on_playing(self, playing: bool) -> None:
        self.btn_play.setIcon(icon("pause" if playing else "play_arrow", C["on_primary"]))

    def _on_track(self, t: dict) -> None:
        self.title_lbl.setText(t["title"])
        self.artist_lbl.setText(t["artist"])
        self._set_album_label()
        self.setWindowTitle(f"{t['title']} — {t['artist']}")
        data = read_cover(t["path"])
        pm = QPixmap()
        has_cover = bool(data) and pm.loadFromData(data)
        self._set_cover(pm if has_cover else None)
        self.mini_title.setText(t["title"])
        self.mini_artist.setText(t["artist"])
        self.lyrics.load(t)
        self._refresh_now_playing_stats()
        self._theme_from_art(pm if has_cover else None)
        self.media.update_track(t, data if has_cover else None)
        self.tracks.set_playing(t["id"])
        row = self.tracks.row_of(t["id"])
        if row >= 0 and self.stack.currentWidget() is self.table:
            self.table.scrollTo(self.tracks.index(row, 1), QAbstractItemView.ScrollHint.EnsureVisible)
        self._save_session()

    def _set_album_label(self) -> None:
        t = self.engine.current_track
        self.album_lbl.setText(
            f'<a href="album" style="color:{C["on_surface_variant"]};text-decoration:none">'
            f'{html.escape(t["album"])}</a>' if t else "")

    def _set_cover(self, pm: QPixmap | None) -> None:
        self._cover_src = pm
        dpr = self.screen().devicePixelRatio() if self.screen() else 1.0
        self.cover.setPixmap(rounded_cover(pm, COVER, 28, dpr))
        self.mini_cover.setPixmap(rounded_cover(pm, 48, 10, dpr))

    def _theme_from_art(self, pm: QPixmap | None, apply: bool = True) -> None:
        """Material You: recolour the app from the cover (only when that theme is chosen)."""
        if self._theme_preset != theme.ALBUM or pm is None or pm.isNull():
            return
        found = theme.seed_from_image(pm.toImage())
        if not found or (found[0], found[1]) == (theme.art_seed, theme.art_vivid):
            return
        theme.art_seed, theme.art_vivid = found
        self.settings.setValue("theme/art_seed", f"{found[0]}|{int(found[1])}")
        if apply:
            self._apply_theme()

    def _set_shuffle(self, on: bool) -> None:
        if on != self.engine.shuffle:
            self.engine.set_shuffle(on)
        self._refresh_modes()

    def _cycle_repeat(self) -> None:
        self.engine.cycle_repeat()
        self._refresh_modes()

    def _refresh_modes(self) -> None:
        shuffle, mode = self.engine.shuffle, self.engine.repeat
        self._set_toggle(self.btn_shuffle, "shuffle", shuffle)
        self._set_toggle(self.btn_repeat, "repeat_one" if mode == RepeatMode.ONE else "repeat",
                         mode != RepeatMode.OFF)
        self.btn_repeat.setToolTip(f"Repeat: {mode.name.lower()} (Ctrl+R)")
        self.act_shuffle.blockSignals(True)
        self.act_shuffle.setChecked(shuffle)
        self.act_shuffle.blockSignals(False)

    @staticmethod
    def _set_toggle(btn: QToolButton, name: str, on: bool) -> None:
        btn.setIcon(icon(name, C["on_secondary_container"] if on else C["on_surface_variant"]))
        btn.setProperty("active", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _on_volume(self, v: int) -> None:
        self.engine.set_volume(v)
        name = "volume_off" if v == 0 else ("volume_down" if v < 50 else "volume_up")
        self.btn_vol.setIcon(icon(name))
        self.btn_vol.setToolTip("Unmute" if v == 0 else "Mute")

    def _toggle_mute(self) -> None:
        if self.vol.value() > 0:
            self._unmute_volume = self.vol.value()
            self.vol.setValue(0)
        else:
            self.vol.setValue(self._unmute_volume or 70)

    def _set_hotkeys(self, on: bool) -> None:
        self.media.set_hotkeys_enabled(on)
        if on and self.media.hotkey_labels:
            self.act_hotkeys.setToolTip(", ".join(self.media.hotkey_labels))

    def _on_media_command(self, cmd: str) -> None:
        e = self.engine
        actions = {
            "play": e.play, "pause": e.pause, "toggle": e.toggle, "stop": e.pause,
            "next": e.next, "previous": e.previous,
            "vol_up": lambda: self.vol.setValue(min(100, self.vol.value() + 5)),
            "vol_down": lambda: self.vol.setValue(max(0, self.vol.value() - 5)),
        }
        fn = actions.get(cmd)
        if fn:
            fn()

    # ---- themes ------------------------------------------------------------
    def theme_state(self) -> tuple[str, str, str | None]:
        return self._theme_preset, self._theme_mode, self._theme_seed

    def set_theme(self, preset: str | None = None, mode: str | None = None, seed: str | None = None) -> None:
        if preset is not None:
            ok = preset in theme.PRESETS or preset in (theme.CUSTOM, theme.ALBUM)
            self._theme_preset = preset if ok else theme.DEFAULT_PRESET
            if self._theme_preset == theme.ALBUM:
                self._theme_from_art(self._cover_src, apply=False)
        if mode in theme.MODES:
            self._theme_mode = mode
        if seed:
            self._theme_seed = seed
        if self._theme_preset == theme.CUSTOM and not self._theme_seed:
            self._theme_preset = theme.DEFAULT_PRESET
        s = self.settings
        s.setValue("theme/preset", self._theme_preset)
        s.setValue("theme/mode", self._theme_mode)
        s.setValue("theme/seed", self._theme_seed or "")
        self._apply_theme()

    def show_theme_dialog(self) -> None:
        if self._theme_dialog is None:
            self._theme_dialog = ThemeDialog(self)
        self._theme_dialog.sync()
        self._theme_dialog.show()
        self._theme_dialog.raise_()
        self._theme_dialog.activateWindow()

    def _on_system_scheme(self, *_args) -> None:
        if self._theme_mode == "system" and not self._theming:
            self._apply_theme()
            if self._theme_dialog is not None and self._theme_dialog.isVisible():
                self._theme_dialog.sync()

    def _apply_theme(self) -> None:
        """Re-theme everything live: palette, style sheet and every colour-baked icon/pixmap."""
        if self._theming:
            return
        self._theming = True
        try:
            theme.apply(QApplication.instance(), self._theme_preset, self._theme_mode, self._theme_seed)
            theme.refresh_bound()
            self._refresh_nav()
            self._refresh_modes()
            self._on_playing(self.engine.is_playing)
            self._on_volume(self.vol.value())
            self._update_output_chip()
            self._refresh_eq_button()
            if self._eq_dialog is not None:
                self._eq_dialog.update()
                self._eq_dialog.curve.update()
            self._set_cover(self._cover_src)
            self._set_album_label()
            for w in (self.stars, self.lyrics.view, self.queue_panel.view.viewport(), self.table.viewport()):
                w.update()
            if self.view["kind"] == "stats":
                self.stats_page.refresh()
            self.tracks.set_playing(self.engine.current_track["id"] if self.engine.current_track else None)
            self.grid.viewport().update()
        finally:
            self._theming = False

    # ---- equalizer -----------------------------------------------------------
    def current_output_name(self) -> str:
        return self._current_out.name if self._current_out else ""

    def _out_id(self) -> str | None:
        return self._current_out.id if self._current_out else None

    def _load_eq_for_device(self, force: bool = False) -> None:
        """With per-device profiles, switching output switches the EQ curve too."""
        if not hasattr(self, "eq_store"):
            return
        dev = self._out_id()
        if not force and (not self.eq_store.per_device or dev == self._eq_device):
            return
        self._eq_device = dev
        self.eq = self.eq_store.load(dev, fallback=None if force else self.eq)
        self.apply_eq()
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    def apply_eq(self) -> None:
        self.engine.set_equalizer(self.eq)
        self._refresh_eq_button()

    def save_eq(self) -> None:
        self.eq_store.save(self._out_id(), self.eq)

    def set_eq_per_device(self, on: bool) -> None:
        self.eq_store.per_device = on
        self._eq_device = self._out_id()
        self.save_eq()                      # current curve becomes this device's (or the shared) profile

    def _refresh_eq_button(self) -> None:
        on = self.eq.enabled and self.engine.supports_eq
        self._set_toggle(self.btn_eq, "equalizer", on)
        self.btn_eq.setToolTip(f"Equalizer: {self.eq.preset if on else 'off'} (Ctrl+E)")

    def restart_app(self) -> None:
        """Relaunch (used after installing libmpv). State is saved by closeEvent first."""
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        if self.close():
            QProcess.startDetached(sys.executable, args)
            QApplication.quit()

    def show_equalizer(self) -> None:
        if self._eq_dialog is None:
            self._eq_dialog = EqualizerDialog(self)
        self._eq_dialog.sync()
        self._eq_dialog.show()
        self._eq_dialog.raise_()
        self._eq_dialog.activateWindow()

    # ---- audio output devices ----------------------------------------------
    def _apply_output(self) -> None:
        """Route audio to the chosen device, or the system default if it isn't connected."""
        target = self.devices.get(self._pinned_id) or self.devices.default()
        if target is not None:
            self.engine.set_audio_device(target.qdevice)
        self._current_out = target
        self._update_output_chip()
        self._load_eq_for_device()

    def _select_output(self, dev_id: str) -> None:
        dev = self.devices.get(dev_id)
        self._pinned_id = dev_id
        self._pinned_name = dev.name if dev else ""
        self.settings.setValue("audio/device", dev_id)
        self.settings.setValue("audio/device_name", self._pinned_name)
        self._apply_output()

    def _update_output_chip(self) -> None:
        d = self._current_out
        name = d.name if d else "No audio output"
        bt = bool(d and d.kind == "bluetooth")
        self.btn_output.setIcon(icon(KIND_ICONS[d.kind] if d else "volume_off",
                                     C["primary"] if bt else C["on_surface_variant"]))
        self.btn_output.setText(self.btn_output.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, 180))
        self.btn_output.setProperty("bt", bt)
        self.btn_output.style().unpolish(self.btn_output)
        self.btn_output.style().polish(self.btn_output)
        tip = [f"Playing on {name}", "Click to choose another output"]
        if self._pinned_id and self.devices.get(self._pinned_id) is None:
            tip.insert(1, f"{self._pinned_name or 'Your chosen device'} is not connected. "
                          "Playback returns to it when it reconnects.")
        if d and d.handsfree:
            tip.insert(1, "This is the hands-free (call) output: mono and low quality. "
                          "Pick the stereo entry for the same device.")
        self.btn_output.setToolTip("\n".join(tip))

    def _fill_output_menu(self, menu: QMenu) -> None:
        menu.clear()
        menu.setToolTipsVisible(True)
        default = self.devices.default()
        label = "System default" + (f" ({default.name})" if default else "")
        act = menu.addAction(icon("check", C["primary"]) if not self._pinned_id else icon("speaker"), label)
        act.triggered.connect(lambda _c=False: self._select_output(""))

        devices = self.devices.devices()
        if devices or self._pinned_id:
            menu.addSeparator()
        for d in devices:
            text = d.name + ("  (hands-free, low quality)" if d.handsfree else "")
            chosen = d.id == self._pinned_id
            act = menu.addAction(icon("check", C["primary"]) if chosen else icon(KIND_ICONS[d.kind]), text)
            if d.kind == "bluetooth":
                act.setToolTip("Bluetooth" + (": call profile, use the stereo entry for music" if d.handsfree else ""))
            act.triggered.connect(lambda _c=False, i=d.id: self._select_output(i))
        if self._pinned_id and self.devices.get(self._pinned_id) is None:
            act = menu.addAction(icon("check", C["primary"]),
                                 f"{self._pinned_name or 'Chosen device'} (not connected)")
            act.setEnabled(False)

        menu.addSeparator()
        add_action(menu, "bluetooth", "Connect a Bluetooth device…", self._open_bluetooth)
        add_action(menu, "settings", "Sound settings…", self._open_sound)
        menu.addSeparator()
        for text, attr in (("Switch to headphones and Bluetooth when they connect", "_auto_switch"),
                           ("Pause when headphones or Bluetooth disconnect", "_pause_on_disconnect")):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(getattr(self, attr))
            act.toggled.connect(lambda on, a=attr: setattr(self, a, on))

    def _on_device_disconnected(self, d: OutputDevice) -> None:
        if self._current_out is None or d.id != self._current_out.id:
            return
        paused = self._pause_on_disconnect and d.personal and self.engine.is_playing
        if paused:
            self.engine.pause()
        self.statusBar().showMessage(f"{d.name} disconnected" + (", playback paused" if paused else ""), 6000)

    def _on_device_connected(self, d: OutputDevice) -> None:
        if d.id == self._pinned_id:
            self.statusBar().showMessage(f"Reconnected to {d.name}", 5000)
        elif self._auto_switch and d.personal and not d.handsfree:
            self._select_output(d.id)
            self.statusBar().showMessage(f"Now playing on {d.name}", 5000)

    def _open_bluetooth(self) -> None:
        if open_bluetooth_settings():
            self.statusBar().showMessage(
                "Pair and connect your device in Bluetooth settings; it will appear here automatically.", 8000)
        else:
            self.statusBar().showMessage("Could not open Bluetooth settings on this system.", 6000)

    def _open_sound(self) -> None:
        if not open_sound_settings():
            self.statusBar().showMessage("Could not open sound settings on this system.", 6000)

    # ---- drag & drop a folder onto the window -----------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                self._add_folder(p)


def resource(*parts: str) -> Path:
    """Path to a bundled file, both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def run() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("Zuqongtech")
    app.setApplicationName("MusicPlayer")
    app.setApplicationDisplayName("Music")
    s = QSettings()
    art = str(s.value("theme/art_seed", "") or "")
    if "|" in art:
        theme.art_seed, theme.art_vivid = art.split("|")[0], art.split("|")[1] == "1"
    theme.apply(app, str(s.value("theme/preset", theme.DEFAULT_PRESET) or theme.DEFAULT_PRESET),
                str(s.value("theme/mode", "dark") or "dark"), str(s.value("theme/seed", "") or "") or None)
    icon_path = resource("assets", "icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    return app.exec()
