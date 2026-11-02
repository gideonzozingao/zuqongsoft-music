"""Zuqongsoft Music: start the app.

    python main.py

The window itself lives in the app/ui package (app/ui/launch.py has run()). This file only adds
what every launch needs first: a Python version check, and crash logging. The built .exe has no
console, so without the log an error would make the app vanish without a trace.
"""
from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import traceback
from pathlib import Path

MIN_PYTHON = (3, 10)
APP_ORG, APP_DIR = "Zuqongtech", "MusicPlayer"      # the same folder the library and settings use


def log_dir() -> Path:
    """Where crash logs go: next to the library database (matches Qt's AppDataLocation)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_ORG / APP_DIR / "logs"


def _show_error(title: str, text: str) -> None:
    """Tell the user, even when no window or console exists yet."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, title, text)
            return
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)     # MB_ICONERROR
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(f"{title}: {text}", file=sys.stderr)


def install_crash_logging() -> None:
    """Write Python errors (and hard crashes) to logs/; show the user one message per session."""
    folder = log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    log_path = folder / "crash.log"
    shown = False

    def hook(exc_type, exc, tb) -> None:
        nonlocal shown
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n{text}")
        except OSError:
            pass
        if sys.stderr is not None:
            sys.__excepthook__(exc_type, exc, tb)
        if not shown:                       # errors inside Qt slots don't stop the app: one message is enough
            shown = True
            _show_error("Garamut Music: something went wrong",
                        f"{exc_type.__name__}: {exc}\n\nDetails were saved to:\n{log_path}\n\n"
                        "Please include that file if you report this problem.")

    sys.excepthook = hook
    # Hard crashes (in Qt, mpv or a driver) never reach Python's hook; faulthandler records where they
    # happened. The file must stay open for the whole run.
    try:
        crash_file = open(folder / "native-crash.log", "a", encoding="utf-8")
        faulthandler.enable(crash_file)
        install_crash_logging.file = crash_file      # keep a reference so it is never closed
    except (OSError, RuntimeError):
        pass


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        _show_error("Garamut Music",
                    f"Garamut Music needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; this is "
                    f"{sys.version_info.major}.{sys.version_info.minor}.")
        return 1
    install_crash_logging()
    from app.ui import run           # imported after the hooks, so start-up errors are logged too
    return run()


if __name__ == "__main__":
    sys.exit(main())