"""Builds the window: panes, bar, menus, and wires up signals."""
from __future__ import annotations

import sys
from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListView, QListWidget, QMenu, QPushButton, QStackedWidget, QTableView, QToolButton, QVBoxLayout, QWidget)
from .. import theme
from ..mini import sleep_menu
from ..models import AlbumDelegate, COL_RATING, COL_TIME, StarDelegate, fmt
from ..panels import LyricsPanel, QueuePanel, StarBar
from ..play_tracker import PlayTracker
from ..responsive import PaneAnimator
from ..stats_page import StatsPage
from ..theme import icon
from ..theme_picker import fill_theme_menu
from .constants import ALBUMS, COVER, SONGS, STATS
from .widgets import JumpSlider, VolumeSlider, icon_button, m3_button


class BuildMixin:
    """Part of MainWindow: builds the window: panes, bar, menus, and wires up signals."""

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
