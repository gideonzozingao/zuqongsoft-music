"""The main window (QtWidgets), styled after Material 3."""
from __future__ import annotations

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow
from pathlib import Path
from .. import system_media, theme
from ..audio_devices import AudioDeviceManager, OutputDevice
from ..engine import create_engine
from ..equalizer import EqState, EqStore
from ..equalizer_ui import EqualizerDialog
from ..fullscreen import FullScreenView, KaraokeDialog
from ..library import Library, ScanWorker
from ..loudness_scan import LoudnessScanner
from ..mini import MiniPlayer, SleepTimer, Tray
from ..models import AlbumModel, CoverCache, TrackModel
from ..radio import Radio
from ..theme import C, icon
from ..theme_picker import ThemeDialog
from .constants import COVER, SONGS
from .builders import BuildMixin
from .session import SessionMixin
from .library_views import LibraryMixin
from .playlists import PlaylistMixin
from .playback import PlaybackMixin
from .extras import ExtrasMixin
from .sound import SoundMixin
from .eq_control import EqualizerMixin
from .devices import OutputMixin
from .layout import ResponsiveMixin
from .theming import ThemeMixin


class MainWindow(BuildMixin, SessionMixin, LibraryMixin, PlaylistMixin, PlaybackMixin, ExtrasMixin, SoundMixin, EqualizerMixin, OutputMixin, ResponsiveMixin, ThemeMixin, QMainWindow):
    """The main window. Its behaviour lives in the mixins, one module per area of the app;
    this class only combines them with QMainWindow and sets up the shared state."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Garamut Music")
        self.resize(1280, 800)
        self.setMinimumSize(560, 560)
        self.setAcceptDrops(True)

        self.settings = QSettings()
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(data_dir / "library.db")
        from .. import lyrics as _lyrics
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
