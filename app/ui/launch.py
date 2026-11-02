"""Application start-up: app identity, icon, theme and the main window."""
from __future__ import annotations

import os
import sys
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from pathlib import Path
from .. import BUNDLED, theme
from .window import MainWindow


def resource(*parts: str) -> Path:
    """Path to a bundled file, both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base.joinpath(*parts)

APP_USER_MODEL_ID = "Zuqongtech.ZuqongsoftMusic"     # must match installer.iss

def _set_app_identity() -> None:
    """An installed copy (the installer puts an uninstaller next to the .exe) registers the same
    AppUserModelID as its Start Menu shortcut, so Windows shows "Zuqongsoft Music" with the right
    icon in the media overlay, notifications and taskbar. Portable copies keep Windows' default."""
    if sys.platform != "win32" or not BUNDLED:
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
