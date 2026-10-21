"""Main window (QtWidgets) for the music player, styled after Material 3."""
from __future__ import annotations

import html
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QEvent, QModelIndex, QProcess, QThread, QRect, QRectF, QSettings, QSize, QStandardPaths, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence, QPainter,
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
from .responsive import PaneAnimator
from .stats_page import StatsPage
from .sound_ui import sound_settings, speaker_settings
from .loudness_scan import LoudnessScanner
from .radio import Radio
from .fullscreen import FullScreenView, KaraokeDialog
from .mini import MiniPlayer, SleepTimer, Tray, sleep_menu
from .equalizer import PRESETS as EQ_PRESETS
from .theme import C, icon

COVER = 252
DRAWER_W, NP_W = 264, 300
WIDE_MIN, MEDIUM_MIN = 1180, 860      # window widths: wide >= 1180 > medium >= 860 > compact
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


class VolumeSlider(JumpSlider):
    """Volume slider that can run past 100%: a tick marks 100% and the boost zone turns a warning colour."""

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.maximum() <= 100:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), 100, self.width() - 16) + 8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["on_surface_variant"]))
        p.drawRoundedRect(QRectF(x - 1, self.height() / 2 - 7, 2, 14), 1, 1)


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
        self.setWindowTitle("Zuqongsoft Music")
        self.resize(1280, 800)
        self.setMinimumSize(560, 560)
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
        self._auto_eq: tuple[dict, EqState] | None = None     # (rule, EQ) while an automatic EQ rule applies
        self._scanner: LoudnessScanner | None = None
        self.loudness_status = ""
        self.radio = Radio(self.library)
        self._radio_session = False             # "Start radio" keeps the queue topped up
        self._fs: FullScreenView | None = None
        self._mini: MiniPlayer | None = None
        self._karaoke_dialog: KaraokeDialog | None = None
        self._karaoke = [False, float(self.settings.value("karaoke/vocal", 0.8)),
                         int(self.settings.value("karaoke/key", 0))]
        self._quitting = False
        self._tray_hint_shown = False
        self.engine.trim_lookup = self.library.trim_for
        self._eq_device: str | None = None
        self._eq_dialog: EqualizerDialog | None = None
        self._cover_src: QPixmap | None = None
        self._cover_side = COVER
        self._mode: str | None = None          # "wide" | "medium" | "compact"
        self._drawer_open = False              # sidebar, when it isn't always shown
        self._screen = "player"                # compact: "player" or "library"
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
        self.sleep = SleepTimer(self.engine, self)
        app_icon = QApplication.windowIcon()
        self.tray = Tray(self, app_icon if not app_icon.isNull() else icon("library_music", C["primary"]))
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

        body = self.body = QHBoxLayout()
        body.setContentsMargins(0, 4, 16, 0)
        body.setSpacing(12)
        self.drawer_w = self._build_drawer()
        self.content_w = self._build_content()
        self.np_w = self._build_now_playing()
        body.addWidget(self.drawer_w)
        body.addWidget(self.content_w, 1)
        body.addWidget(self.np_w)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_bar())
        self.panes = PaneAnimator(body, [self.drawer_w, self.content_w, self.np_w], self)
        self.panes.finished.connect(self._after_layout)
        self._cover_timer = QTimer(self, singleShot=True, interval=40)
        self._cover_timer.timeout.connect(self._fit_cover)
        for w in (self.np_w, self.table, self.btn_vol):
            w.installEventFilter(self)

    def _build_drawer(self) -> QWidget:
        drawer = QWidget(objectName="drawer")
        drawer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        brand.addWidget(QLabel("Zuqongsoft Music", objectName="brand"), 1)
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
        self.search.setMinimumWidth(110)
        self.search.setMaximumWidth(340)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.btn_menu = icon_button("menu", "Show sidebar (Ctrl+B)", role="on_surface")
        self.btn_menu.hide()
        head.addWidget(self.btn_menu, 0, Qt.AlignmentFlag.AlignTop)
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
        if self._mode == "compact" and self._screen != "player":
            self.show_screen("player")
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
        self.vol = VolumeSlider(Qt.Orientation.Horizontal)
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

        self.btn_screen = icon_button("library_music", "Show your library")
        self.btn_screen.hide()
        left = self.bar_left = QWidget()
        left.setFixedWidth(230)
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        ll.addWidget(self.btn_screen)
        ll.addWidget(self.btn_output)
        ll.addStretch(1)
        right = self.bar_right = QWidget()
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
        menu_action(m, "Show &Sidebar", self.toggle_drawer, "Ctrl+B")
        menu_action(m, "&Full Screen", self.show_fullscreen, "F11")
        menu_action(m, "&Mini Player", self.show_mini, "Ctrl+M")
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
        self.act_dj = menu_action(m, "&DJ Mode (crossfades)", self._toggle_dj, "Ctrl+D", checkable=True)
        self.act_autoplay = menu_action(m, "&Autoplay Similar Songs", self._set_autoplay, checkable=True)
        menu_action(m, "&Karaoke…", self.show_karaoke, "Ctrl+K")
        self.sleep_menu = m.addMenu("S&leep Timer")
        theme.bind(self.sleep_menu.menuAction(), "bedtime")
        self.act_tray = menu_action(m, "Keep Playing in the &Tray When Closed", self._set_keep_in_tray, checkable=True)
        m.addSeparator()
        menu_action(m, "&Equalizer…", self.show_equalizer, "Ctrl+E")
        menu_action(m, "&Sound Settings…", lambda: self.show_equalizer(page=2), "Ctrl+Shift+E")
        menu_action(m, "Studio &EQ…", lambda: self.show_equalizer(page=1), "Ctrl+Alt+E")
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
        e.queueChanged.connect(lambda: QTimer.singleShot(0, self._radio_top_up))
        e.positionChanged.connect(self._on_position_extra)
        e.playingChanged.connect(self._on_playing_extra)
        self.lyrics.lyricsChanged.connect(lambda: self._fs.sync_lyrics() if self._fs and self._fs.isVisible() else None)
        self.sleep.changed.connect(self._on_sleep_status)
        self.sleep_menu.aboutToShow.connect(lambda: sleep_menu(self.sleep_menu, self.sleep))
        self.queue_panel.btn_autoplay.toggled.connect(self._set_autoplay)
        self.cover.installEventFilter(self)
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
        self.btn_menu.clicked.connect(self.toggle_drawer)
        self.btn_screen.clicked.connect(lambda: self.show_screen("library" if self._screen == "player" else "player"))
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
        self.vol.setValue(min(100, int(self.settings.value("volume", 70))))   # never start boosted
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
        for act, key in ((self.act_dj, "dj/enabled"), (self.act_autoplay, "playback/autoplay"),
                         (self.act_tray, "ui/keep_in_tray")):
            act.blockSignals(True)
            act.setChecked(self.settings.value(key, False, type=bool))
            act.blockSignals(False)
        self.queue_panel.btn_autoplay.blockSignals(True)
        self.queue_panel.btn_autoplay.setChecked(self.act_autoplay.isChecked())
        self.queue_panel.btn_autoplay.blockSignals(False)
        self._apply_output()
        self.engine.set_normalize(self.eq_store.normalize)
        self.apply_sound_settings()
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
        if (not self._quitting and self.settings.value("ui/keep_in_tray", False, type=bool)
                and self.tray.tray.isVisible()):
            event.ignore()                       # keep playing; the tray icon brings the window back
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.tray.showMessage("Zuqongsoft Music", "Still playing here. Right-click the icon for "
                                           "controls, or choose Quit.", QIcon(), 4000)
            return
        for w in (self._fs, self._mini):
            if w is not None:
                w.close()
        self.tray.tray.hide()
        self._save_session()                 # before stop() resets the position
        self.tracker.finish(closing=True)
        self.stop_loudness_scan()
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
        s.setValue("volume", min(100, self.vol.value()))
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
            self._after_navigation()

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        # Clicking "Albums" while inside an album goes back to the grid.
        view = item.data(Qt.ItemDataRole.UserRole)
        if view and view["kind"] == "albums" and self.view["kind"] == "album":
            self._navigate(ALBUMS)

    def _navigate(self, view: dict) -> None:
        self.view = dict(view)
        self._refresh_nav()
        self._reload()
        self._after_navigation()

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
            if w.changed and (self.settings.value("sound/level", False, type=bool)
                              or self.settings.value("dj/enabled", False, type=bool)):
                self.start_loudness_scan()
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
        if len(sel) == 1:
            add_action(m, "radio", "Start radio from this song", lambda: self.start_radio(sel[0]))
        self._queue_actions(m, sel)
        m.addSeparator()
        self._playlist_submenu(m, [t["path"] for t in sel])
        self._rate_submenu(m, sel)
        if len(sel) == 1 and self.engine.supports_eq:
            self._auto_eq_menu(m, sel[0])
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
        self._radio_session = False
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
        self._apply_track_level(t)
        self._apply_auto_eq(t)
        art = pm if has_cover else None
        if self._fs is not None:
            self._fs.set_track(t, art)
        if self._mini is not None:
            self._mini.set_track(t, art)
        self.tray.set_track(t, self.sleep.remaining_text())
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
        self.cover.setPixmap(rounded_cover(pm, self._cover_side, 28, dpr))
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

    def apply_sound_settings(self) -> None:
        """Push the Sound page settings to the engine and the volume slider."""
        s = sound_settings(self.settings)
        top = self.engine.max_volume if s["boost"] else 100
        if self.vol.value() > top:
            self.vol.setValue(top)
        self.vol.setMaximum(top)
        self.vol.update()
        self.engine.set_loudness(s["loudness"])
        self.engine.set_sound_options(s["exclusive"], s["hq_resample"], s["channels"])
        self.engine.set_speaker(speaker_settings(self.settings))
        level = self.settings.value("sound/level", False, type=bool) and self.engine.supports_eq
        self.engine.set_normalize(self.eq_store.normalize and not level)     # levelling replaces ReplayGain
        self._apply_track_level(self.engine.current_track)
        st = self.settings
        dj = st.value("dj/enabled", False, type=bool)
        trim = dj and st.value("dj/trim", True, type=bool)
        self.engine.set_dj(int(float(st.value("dj/crossfade", 6)) * 1000) if dj else 0, trim,
                           dj and st.value("dj/fades", True, type=bool), st.value("dj/keep_albums", True, type=bool))
        if level or trim:
            self.start_loudness_scan()          # measures loudness and silent intros/outros

    # ---- DJ mode, autoplay, radio -----------------------------------------------------
    def _toggle_dj(self, on: bool) -> None:
        self.settings.setValue("dj/enabled", on)
        self.apply_sound_settings()
        xf = float(self.settings.value("dj/crossfade", 6))
        self.statusBar().showMessage(f"DJ mode on: {xf:g} s crossfades, smooth fades" if on else "DJ mode off", 4000)
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    def _set_autoplay(self, on: bool) -> None:
        self.settings.setValue("playback/autoplay", on)
        for w in (self.act_autoplay, self.queue_panel.btn_autoplay):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        if on:
            self._radio_top_up()

    def start_radio(self, seed: dict) -> None:
        picks = self.radio.pick([seed], {seed["path"]}, 9)
        self._radio_session = False
        self._queue_source = dict(SONGS)
        self.engine.set_queue([seed] + picks, 0)
        self._radio_session = True
        self.statusBar().showMessage(f"Radio based on “{seed['title']}”: songs that fit will keep coming", 5000)
        self._set_np_tab(2)

    def _radio_top_up(self) -> None:
        """Keep at least three songs coming when autoplay or a radio is on."""
        if not (self._radio_session or self.settings.value("playback/autoplay", False, type=bool)):
            return
        cur = self.engine.current_track
        upcoming = [t for t, _u in self.engine.upcoming_items()]
        if cur is None or len(upcoming) >= 3:
            return
        seeds = [cur] + upcoming[-3:]
        exclude = {t["path"] for t in self.engine._queue}
        picks = self.radio.pick(seeds, exclude, 8)
        if picks:
            self.engine.extend(picks)

    # ---- karaoke ------------------------------------------------------------------------
    def karaoke_state(self) -> tuple[bool, float, int]:
        return self._karaoke[0], self._karaoke[1], self._karaoke[2]

    def set_karaoke(self, on: bool | None = None, vocal: float | None = None, key: int | None = None) -> None:
        if not self.engine.supports_eq:
            self.statusBar().showMessage("Karaoke needs the mpv audio engine (see Playback > Equalizer).", 6000)
            return
        k = self._karaoke
        if on is not None:
            k[0] = on
        if vocal is not None:
            k[1] = max(0.0, min(1.0, vocal))
            self.settings.setValue("karaoke/vocal", k[1])
        if key is not None:
            k[2] = max(-6, min(6, int(key)))
            self.settings.setValue("karaoke/key", k[2])
        self.engine.set_karaoke(k[1] if k[0] else 0.0, k[2] if k[0] else 0)
        for w in (self._karaoke_dialog.controls if self._karaoke_dialog else None,):
            if w is not None:
                w.sync()
        if self._fs is not None:
            self._fs.refresh_karaoke()

    def show_karaoke(self) -> None:
        if self._karaoke_dialog is None:
            self._karaoke_dialog = KaraokeDialog(self)
        self._karaoke_dialog.controls.sync()
        self._karaoke_dialog.show()
        self._karaoke_dialog.raise_()

    # ---- full screen, mini player, tray ---------------------------------------------------
    def show_fullscreen(self) -> None:
        if self._fs is None:
            self._fs = FullScreenView(self)
            self._fs.closed.connect(self._fullscreen_closed)
        t = self.engine.current_track
        self._fs.set_track(t, self._cover_src)
        self.lyrics.set_active(True)                 # look lyrics up online while full screen
        self._fs.sync_lyrics()
        self._fs.set_playing(self.engine.is_playing)
        self._fs.refresh_karaoke()
        self._fs.open()

    def _fullscreen_closed(self) -> None:
        self.lyrics.set_active(self.np_stack.currentIndex() == 1)
        studio_open = self._eq_dialog is not None and self._eq_dialog.isVisible() \
            and self._eq_dialog.pages.currentIndex() == 1 and self._eq_dialog.studio.spectrum.isChecked()
        self.engine.set_rta(studio_open)

    def show_mini(self) -> None:
        if self._mini is None:
            self._mini = MiniPlayer(self)
            self._mini.expandRequested.connect(self.show_main)
            pos = self.settings.value("mini/pos")
            if pos is not None:
                self._mini.move(pos)
            else:
                geo = self.screen().availableGeometry()
                self._mini.move(geo.right() - self._mini.width() - 24, geo.bottom() - self._mini.height() - 24)
        self._mini.set_track(self.engine.current_track, self._cover_src)
        self._mini.set_playing(self.engine.is_playing)
        self._mini.show()
        self._mini.raise_()
        self.hide()

    def show_main(self) -> None:
        if self._mini is not None:
            self._mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_main(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_main()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()
        QApplication.quit()

    def _set_keep_in_tray(self, on: bool) -> None:
        self.settings.setValue("ui/keep_in_tray", on)

    def _on_sleep_status(self, text: str) -> None:
        if not hasattr(self, "sleep_lbl"):
            self.sleep_lbl = QPushButton("")
            self.sleep_lbl.setToolTip("Sleep timer: click to cancel")
            theme.bind(self.sleep_lbl, "bedtime", "primary")
            self.sleep_lbl.clicked.connect(lambda: self.sleep.cancel())
            self.statusBar().addPermanentWidget(self.sleep_lbl)
        self.sleep_lbl.setVisible(bool(text))
        self.sleep_lbl.setText(f"Sleep {text}" if text.startswith("after") else f"Sleep in {text}")
        self.tray.set_track(self.engine.current_track, text)

    def _on_position_extra(self, ms: int) -> None:
        if self._fs is not None and self._fs.isVisible():
            self._fs.lyrics.set_position(ms, self.engine.is_playing)

    def _on_playing_extra(self, playing: bool) -> None:
        if self._fs is not None:
            self._fs.set_playing(playing)
        if self._mini is not None:
            self._mini.set_playing(playing)

    # ---- library loudness levelling ------------------------------------------------
    def _apply_track_level(self, t: dict | None) -> None:
        if not self.settings.value("sound/level", False, type=bool) or not self.engine.supports_eq:
            self.engine.set_track_gain(None)
            return
        target = float(self.settings.value("sound/level_target", -14.0))
        lufs = self.library.loudness_for(t["path"]) if t else None
        self.engine.set_track_gain(max(-20.0, min(12.0, target - lufs)) if lufs is not None else 0.0)

    def start_loudness_scan(self) -> None:
        if self._scanner is not None and self._scanner.isRunning():
            return
        if not self.engine.supports_eq or not self.library.unscanned_tracks():
            done, total = self.library.loudness_progress()
            self.loudness_status = f"All {total} songs measured" if total else ""
            return
        self._scanner = LoudnessScanner(self.library, self)
        self._scanner.progress.connect(self._scan_progress)
        self._scanner.measured.connect(self._scan_measured)
        self._scanner.start(QThread.Priority.LowPriority)

    def stop_loudness_scan(self) -> None:
        if self._scanner is not None and self._scanner.isRunning():
            self._scanner.requestInterruption()
            self._scanner.wait(3000)

    def _scan_progress(self, done: int, total: int) -> None:
        self.loudness_status = (f"Measured {done:,} of {total:,} songs" if done < total
                                else f"All {total:,} songs measured")

    def _scan_measured(self, path: str, _lufs) -> None:
        cur = self.engine.current_track
        if cur and cur["path"] == path:
            self._apply_track_level(cur)

    def _boost_allowed_now(self, v: int) -> bool:
        """One-time hearing warning the first time you go past 100% on headphones or Bluetooth."""
        out = self._current_out
        if v <= 100 or not (out and out.personal) or self.settings.value("sound/boost_warned", False, type=bool):
            return True
        self.vol.dragging = False
        self.vol.blockSignals(True)
        self.vol.setValue(100)
        self.vol.blockSignals(False)
        self.engine.set_volume(100)
        if not getattr(self, "_asking_boost", False):
            self._asking_boost = True
            QTimer.singleShot(0, lambda: self._ask_boost(v))
        return False

    def _ask_boost(self, v: int) -> None:
        answer = QMessageBox.warning(
            self, "Volume above 100%",
            f"You're listening on {self._current_out.name if self._current_out else 'headphones'}.\n\n"
            "Going above 100% can be very loud on headphones, and listening at high volume for long "
            "periods can permanently damage your hearing.\n\nGo above 100% anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        self._asking_boost = False
        if answer == QMessageBox.StandardButton.Yes:
            self.settings.setValue("sound/boost_warned", True)
            self.vol.setValue(v)

    def _on_volume(self, v: int) -> None:
        if not self._boost_allowed_now(v):
            return
        boosted = v > 100
        if self.vol.property("boost") != boosted:
            self.vol.setProperty("boost", boosted)
            self.vol.style().unpolish(self.vol)
            self.vol.style().polish(self.vol)
        if boosted and self.vol.isVisible():
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(self.vol.mapToGlobal(self.vol.rect().topRight()), f"{v}% (boost)", self.vol)
        self.engine.set_volume(v)
        name = "volume_off" if v == 0 else ("volume_down" if v < 50 else "volume_up")
        self.btn_vol.setIcon(icon(name))
        self.btn_vol.setToolTip(("Unmute" if v == 0 else "Mute") + f" (volume {v}%, scroll to change)")

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
            "vol_up": lambda: self.vol.setValue(min(self.vol.maximum(), self.vol.value() + 5)),
            "vol_down": lambda: self.vol.setValue(max(0, self.vol.value() - 5)),
        }
        fn = actions.get(cmd)
        if fn:
            fn()

    # ---- responsive layout -------------------------------------------------
    def _mode_for(self, width: int) -> str:
        return "wide" if width >= WIDE_MIN else "medium" if width >= MEDIUM_MIN else "compact"

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._check_mode()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._check_mode()

    def _check_mode(self) -> None:
        mode = self._mode_for(self.width())
        if mode == self._mode:
            return
        first = self._mode is None
        self._mode = mode
        self._drawer_open = False              # the sidebar always starts folded away when space shrinks
        self._apply_layout(animate=not first and self.isVisible())

    def _plan(self) -> dict:
        d, c, n = self.drawer_w, self.content_w, self.np_w
        hidden, stretch = ("hidden",), ("stretch",)
        if self._mode == "wide":
            return {d: ("fixed", DRAWER_W), c: stretch, n: ("fixed", NP_W)}
        drawer = ("fixed", DRAWER_W) if self._drawer_open else hidden
        if self._mode == "medium":
            return {d: drawer, c: stretch, n: ("fixed", NP_W)}
        if self._screen == "library":
            return {d: drawer, c: stretch, n: hidden}
        return {d: hidden, c: hidden, n: stretch}

    def _apply_layout(self, animate: bool = True) -> None:
        plan = self._plan()
        left = 0 if plan[self.drawer_w][0] != "hidden" else 16
        self._update_chrome()
        self.panes.apply(plan, self.centralWidget().width(), left, animate)

    def _update_chrome(self) -> None:
        """Buttons and bar contents that depend on the layout mode."""
        compact = self._mode == "compact"
        self.btn_menu.setVisible(self._mode != "wide")
        self.btn_menu.setToolTip(("Hide" if self._drawer_open else "Show") + " sidebar (Ctrl+B)")
        self.btn_screen.setVisible(compact)
        on_player = self._screen == "player"
        theme.bind(self.btn_screen, "library_music" if on_player else "music_note")
        self.btn_screen.setToolTip("Show your library" if on_player else "Show what's playing")
        self.bar_left.setFixedWidth(96 if compact else 230)
        self.bar_right.setFixedWidth(96 if compact else 230)
        self.vol.setVisible(not compact)
        self.btn_output.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                           else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        centre = compact and on_player
        align = Qt.AlignmentFlag.AlignHCenter if centre else Qt.AlignmentFlag.AlignLeft
        for lbl in (self.title_lbl, self.artist_lbl, self.album_lbl, self.plays_lbl):
            lbl.setAlignment(align)
        lay = self.cover.parentWidget().layout()
        for w in (self.cover, self.stars):
            lay.setAlignment(w, Qt.AlignmentFlag.AlignHCenter if centre else Qt.AlignmentFlag.AlignLeft)

    def _after_layout(self) -> None:
        self._fit_cover()
        self._fit_columns()

    def toggle_drawer(self) -> None:
        if self._mode == "wide":
            return
        self._drawer_open = not self._drawer_open
        if self._drawer_open and self._mode == "compact":
            self._screen = "library"
        self._apply_layout()

    def show_screen(self, screen: str) -> None:
        """Compact layout: switch between the player and the library."""
        if self._mode != "compact" or screen == self._screen:
            return
        self._screen = screen
        if screen == "player":
            self._drawer_open = False
        self._apply_layout()

    def _after_navigation(self) -> None:
        changed = False
        if self._mode in ("medium", "compact") and self._drawer_open:
            self._drawer_open = False
            changed = True
        if self._mode == "compact" and self._screen != "library":
            self._screen = "library"
            changed = True
        if changed and self._mode is not None:
            self._apply_layout()

    def _fit_cover(self) -> None:
        """The big cover grows with the player pane (it fills the window in compact mode)."""
        page = self.cover.parentWidget()
        free_h = page.height() - 190          # room left for title, artist, stars below
        side = int(max(160, min(self.np_w.width() - 48, free_h, 520)))
        if side != self._cover_side:
            self._cover_side = side
            self.cover.setFixedSize(side, side)
            self._set_cover(self._cover_src)

    def _fit_columns(self) -> None:
        w = self.table.viewport().width()
        self.table.setColumnHidden(3, w < 620)            # Album
        self.table.setColumnHidden(COL_RATING, w < 480)
        self.table.setColumnHidden(2, w < 340)            # Artist

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.cover and ev.type() == QEvent.Type.MouseButtonDblClick:
            self.show_fullscreen()
            return True
        if ev.type() == QEvent.Type.Resize:
            if obj is self.np_w and not self.panes.running:
                self._cover_timer.start()
            elif obj is self.table:
                self._fit_columns()
        elif ev.type() == QEvent.Type.Wheel and obj is self.btn_vol:
            step = 5 if ev.angleDelta().y() > 0 else -5     # scroll on the speaker icon = volume
            self.vol.setValue(max(0, min(self.vol.maximum(), self.vol.value() + step)))
            return True
        return super().eventFilter(obj, ev)

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
            if self._mini is not None:
                self._mini.refresh_theme()
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
        self.engine.set_equalizer(self._auto_eq[1] if self._auto_eq else self.eq)
        self._refresh_eq_button()

    # ---- automatic EQ (per genre / album / song) --------------------------------------
    def auto_rules(self) -> list[dict]:
        try:
            rules = json.loads(self.settings.value("eq/auto_rules", "") or "[]")
        except (TypeError, ValueError):
            rules = []
        return [r for r in rules if isinstance(r, dict) and r.get("kind") in ("song", "album", "genre")]

    def set_auto_rules(self, rules: list[dict]) -> None:
        self.settings.setValue("eq/auto_rules", json.dumps(rules))
        self._apply_auto_eq(self.engine.current_track)
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    @staticmethod
    def _album_key(t: dict) -> str:
        return f"{t.get('album', '')}\x1f{t.get('album_artist') or t.get('artist', '')}"

    def _rule_for(self, t: dict | None) -> dict | None:
        if not t:
            return None
        rules = self.auto_rules()
        for kind, key in (("song", t.get("path", "")), ("album", self._album_key(t)),
                          ("genre", (t.get("genre") or "").casefold())):
            for r in rules:
                if r["kind"] == kind and key and (r["key"].casefold() if kind == "genre" else r["key"]) == key:
                    return r
        return None

    def _preset_gains(self, name: str) -> list[float] | None:
        if name in EQ_PRESETS:
            return [float(g) for g in EQ_PRESETS[name]]
        user = self.eq_store.user_presets().get(name)
        return [float(g) for g in user] if user else None

    def _apply_auto_eq(self, t: dict | None) -> None:
        rule = self._rule_for(t)
        gains = self._preset_gains(rule["preset"]) if rule else None
        if rule and gains is not None:
            override = self.eq.copy()
            override.enabled, override.mode, override.gains, override.preset = True, "simple", gains, rule["preset"]
            changed = not self._auto_eq or self._auto_eq[0] != rule
            self._auto_eq = (rule, override)
            self.apply_eq()
            if changed:
                self.statusBar().showMessage(f"Automatic EQ: {rule['preset']} (for {rule['label']})", 5000)
        elif self._auto_eq:
            self._auto_eq = None
            self.apply_eq()

    def clear_auto_eq(self) -> None:
        """Editing the EQ by hand takes over from an automatic rule for the current song."""
        self._auto_eq = None

    def add_auto_rule(self, kind: str, key: str, label: str, preset: str | None) -> None:
        rules = [r for r in self.auto_rules() if not (r["kind"] == kind and r["key"] == key)]
        if preset:
            rules.append({"kind": kind, "key": key, "label": label, "preset": preset})
        self.set_auto_rules(rules)

    def _auto_eq_menu(self, menu: QMenu, t: dict) -> None:
        sub = menu.addMenu(icon("equalizer"), "Automatic EQ")
        presets = list(EQ_PRESETS) + sorted(self.eq_store.user_presets(), key=str.casefold)
        current = {r["kind"]: r for r in self.auto_rules()
                   if r["key"] in (t["path"], self._album_key(t), (t.get("genre") or "").casefold())
                   or (r["kind"] == "genre" and r["key"].casefold() == (t.get("genre") or "").casefold())}
        targets = [("song", t["path"], f"“{t['title']}”", "For this song"),
                   ("album", self._album_key(t), f"the album {t['album']}", "For this album")]
        if t.get("genre"):
            targets.append(("genre", t["genre"].casefold(), f"genre {t['genre']}", f"For genre “{t['genre']}”"))
        for kind, key, label, text in targets:
            m = sub.addMenu(text)
            now = current.get(kind)
            for name in presets:
                a = m.addAction(name)
                a.setCheckable(True)
                a.setChecked(bool(now and now["preset"] == name))
                a.triggered.connect(lambda _c=False, k=kind, ky=key, lb=label, n=name: self.add_auto_rule(k, ky, lb, n))
            m.addSeparator()
            off = m.addAction("No automatic EQ")
            off.setEnabled(bool(now))
            off.triggered.connect(lambda _c=False, k=kind, ky=key, lb=label: self.add_auto_rule(k, ky, lb, None))

    def save_eq(self) -> None:
        self.eq_store.save(self._out_id(), self.eq)

    def set_eq_per_device(self, on: bool) -> None:
        self.eq_store.per_device = on
        self._eq_device = self._out_id()
        self.save_eq()                      # current curve becomes this device's (or the shared) profile

    def _refresh_eq_button(self) -> None:
        eff = self._auto_eq[1] if getattr(self, "_auto_eq", None) else self.eq
        on = eff.enabled and self.engine.supports_eq
        self._set_toggle(self.btn_eq, "equalizer", on)
        eff = self._auto_eq[1] if self._auto_eq else self.eq
        what = f"Studio EQ ({eff.studio.name})" if eff.studio_on else eff.preset
        if self._auto_eq:
            what += f", automatic for {self._auto_eq[0]['label']}"
        self.btn_eq.setToolTip(f"Equalizer: {what if eff.enabled else 'off'} (Ctrl+E)")

    def restart_app(self) -> None:
        """Relaunch (used after installing libmpv). State is saved by closeEvent first."""
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        if self.close():
            QProcess.startDetached(sys.executable, args)
            QApplication.quit()

    def show_equalizer(self, page: int = 0) -> None:
        if self._eq_dialog is None:
            self._eq_dialog = EqualizerDialog(self)
        self._eq_dialog.sync()
        self._eq_dialog.show_page(page)
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
        chip_font = QFont(self.btn_output.font())          # the chip's style sheet font: 13 px, semibold
        chip_font.setPixelSize(13)
        chip_font.setWeight(QFont.Weight.DemiBold)
        self.btn_output.setText(QFontMetrics(chip_font).elidedText(name, Qt.TextElideMode.ElideRight, 170))
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


APP_USER_MODEL_ID = "Zuqongtech.ZuqongsoftMusic"     # must match installer.iss


def _set_app_identity() -> None:
    """An installed copy (the installer puts an uninstaller next to the .exe) registers the same
    AppUserModelID as its Start Menu shortcut, so Windows shows "Zuqongsoft Music" with the right
    icon in the media overlay, notifications and taskbar. Portable copies keep Windows' default."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        folder = os.path.dirname(sys.executable)
        if any(n.lower().startswith("unins") and n.lower().endswith(".exe") for n in os.listdir(folder)):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def run() -> int:
    _set_app_identity()                          # must happen before any window exists
    app = QApplication(sys.argv)
    app.setOrganizationName("Zuqongtech")
    app.setApplicationName("MusicPlayer")
    app.setApplicationDisplayName("Zuqongsoft Music")
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