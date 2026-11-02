"""In-app updates from GitHub Releases.

Flow: check the latest *published* release (drafts and pre-releases are ignored) -> if it is newer,
show its notes -> download the Setup .exe -> verify its SHA-256 against the release's .sha256 file
-> run the installer in silent upgrade mode and quit; the installer reopens the app when done.

Safety rules:
- Only HTTPS downloads from the release's own assets.
- No checksum file, or a checksum that doesn't match: never install automatically; offer the
  release page instead.
- Only installed copies update themselves (an uninstaller sits next to the .exe). Copies run from
  source or a zip get a link to the download page.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout)

from . import BUNDLED

UPDATE_REPO = "gideonzozingao/zuqongsoft-music"      # GitHub redirects the API if the repo is renamed
API_LATEST = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
CHECK_EVERY = 24 * 3600                               # automatic checks: at most once a day
USER_AGENT = "GaramutMusic-Updater"


# ---- versions ---------------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.0.4' / '1.0.4.0' -> (1, 0, 4). None if it isn't a version."""
    m = re.search(r"(\d+(?:\.\d+)+)", text or "")
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _exe_version(path: str) -> tuple[int, ...] | None:
    """The file version Windows shows in Properties (set by version_info.txt / Nuitka)."""
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents   # VS_FIXEDFILEINFO
        ms, ls = info[2], info[3]
        return parse_version(f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}")
    except Exception:
        return None


def current_version() -> tuple[int, ...] | None:
    """This copy's version: from the .exe when built, from version_info.txt when run from source."""
    if BUNDLED and sys.platform == "win32":
        v = _exe_version(sys.executable)
        if v:
            return v
    try:
        text = (Path(__file__).resolve().parent.parent / "version_info.txt").read_text(encoding="utf-8-sig")
        m = re.search(r"StringStruct\('ProductVersion',\s*'([\d.]+)'\)", text)
        return parse_version(m.group(1)) if m else None
    except OSError:
        return None


def version_text(v: tuple[int, ...] | None) -> str:
    return ".".join(str(p) for p in v) if v else "unknown"


def is_installed_copy() -> bool:
    """Installed by the Setup (its uninstaller sits next to the .exe), so it can update itself."""
    if sys.platform != "win32" or not BUNDLED:
        return False
    folder = os.path.dirname(sys.executable)
    try:
        return any(n.lower().startswith("unins") and n.lower().endswith(".exe") for n in os.listdir(folder))
    except OSError:
        return False


# ---- the release -------------------------------------------------------------------------------
@dataclass
class Release:
    version: tuple[int, ...]
    tag: str
    name: str
    notes: str
    page_url: str
    setup_name: str = ""
    setup_url: str = ""
    setup_size: int = 0
    sha_url: str = ""

    @property
    def can_auto_install(self) -> bool:
        return bool(self.setup_url and self.sha_url)


def release_from_json(data: dict) -> Release | None:
    if data.get("draft") or data.get("prerelease"):
        return None
    version = parse_version(data.get("tag_name", ""))
    if not version:
        return None
    rel = Release(version, data.get("tag_name", ""), data.get("name") or data.get("tag_name", ""),
                  data.get("body") or "", data.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases")
    assets = data.get("assets") or []
    setup = next((a for a in assets if a.get("name", "").lower().endswith(".exe")
                  and "setup" in a.get("name", "").lower()), None)
    if setup:
        rel.setup_name, rel.setup_url, rel.setup_size = setup["name"], setup["browser_download_url"], setup.get("size", 0)
        sha = next((a for a in assets if a.get("name", "").lower() == setup["name"].lower() + ".sha256"), None)
        if sha:
            rel.sha_url = sha["browser_download_url"]
    return rel


def _get(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


class CheckWorker(QThread):
    """Fetches the latest release. done(release or None, error text or '')."""
    done = Signal(object, str)

    def run(self) -> None:
        try:
            with _get(API_LATEST) as r:
                data = json.loads(r.read().decode("utf-8"))
            self.done.emit(release_from_json(data), "")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.done.emit(None, "")                     # no published release yet
            elif e.code in (403, 429):
                self.done.emit(None, "GitHub is limiting update checks right now. Try again in an hour.")
            else:
                self.done.emit(None, f"The update server answered {e.code}.")
        except Exception as e:                               # offline, DNS, TLS, bad JSON
            self.done.emit(None, f"Couldn't reach the update server ({type(e).__name__}).")


class DownloadWorker(QThread):
    """Downloads the Setup and verifies its checksum. progress(done, total); finished_ok(path); failed(text)."""
    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, release: Release, parent=None) -> None:
        super().__init__(parent)
        self.release = release

    def run(self) -> None:
        rel = self.release
        folder = Path(tempfile.gettempdir()) / "GaramutMusic-update"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with _get(rel.sha_url) as r:
                expected = r.read().decode("utf-8-sig").split()[0].strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                self.failed.emit("The release's checksum file is not valid.")
                return
            target = folder / rel.setup_name
            part = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            with _get(rel.setup_url, timeout=60) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or rel.setup_size or 0)
                done = 0
                while True:
                    if self.isInterruptionRequested():
                        f.close()
                        part.unlink(missing_ok=True)
                        return
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total)
            if digest.hexdigest() != expected:
                part.unlink(missing_ok=True)
                self.failed.emit("The download didn't match the release's checksum, so it was deleted. "
                                 "Please try again.")
                return
            part.replace(target)
            self.finished_ok.emit(str(target))
        except Exception as e:
            self.failed.emit(f"The download failed ({type(e).__name__}). Check your connection and try again.")


def launch_installer(setup_path: str) -> None:
    """Silent upgrade: Inno Setup shows a small progress window, closes the app if it is still open,
    keeps the install for the same users as before, and reopens the app when finished."""
    exe_dir = os.path.dirname(sys.executable).lower()
    program_files = [os.environ.get(k, "").lower() for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    scope = "/ALLUSERS" if any(p and exe_dir.startswith(p) for p in program_files) else "/CURRENTUSER"
    flags = 0x00000008 | 0x00000200                      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([setup_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", scope],
                     close_fds=True, creationflags=flags)


# ---- the window --------------------------------------------------------------------------------
class UpdateDialog(QDialog):
    def __init__(self, window, release: Release, current: tuple[int, ...] | None) -> None:
        super().__init__(window)
        self.win, self.release, self.worker = window, release, None
        app_name = QApplication.applicationDisplayName() or "the app"
        self.setWindowTitle("Update available")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel(f"{app_name} {version_text(release.version)} is available", objectName="dialogTitle"))
        sub = QLabel(f"You have {version_text(current)}.", objectName="muted")
        lay.addWidget(sub)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or "No release notes.")
        notes.setMinimumHeight(260)
        lay.addWidget(notes, 1)
        self.bar = QProgressBar()
        self.bar.hide()
        self.status = QLabel("", objectName="muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_skip = QPushButton("Skip this version")
        self.btn_later = QPushButton("Later")
        self.btn_go = QPushButton()
        self.btn_go.setProperty("kind", "filled")
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_go)
        lay.addLayout(row)
        self.btn_skip.clicked.connect(self._skip)
        self.btn_later.clicked.connect(self.reject)

        if is_installed_copy() and release.can_auto_install:
            self.btn_go.setText("Install now")
            self.btn_go.clicked.connect(self._install)
            self.status.setText("The app closes during the update and opens again when it's done. "
                                "Your library, playlists and settings are kept.")
        else:
            self.btn_go.setText("Open download page")
            self.btn_go.clicked.connect(self._open_page)
            if is_installed_copy() and not release.can_auto_install:
                self.status.setText("This release has no checksum file, so it can't be installed automatically. "
                                    "Download it from the release page.")

    def _skip(self) -> None:
        self.win.settings.setValue("updates/skipped", self.release.tag)
        self.reject()

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.release.page_url))
        self.accept()

    def _install(self) -> None:
        for b in (self.btn_go, self.btn_skip):
            b.setEnabled(False)
        self.btn_later.setText("Cancel")
        self.bar.setRange(0, 0)
        self.bar.show()
        self.status.setText("Downloading the update…")
        self.worker = DownloadWorker(self.release, self)
        self.worker.progress.connect(self._progress)
        self.worker.failed.connect(self._failed)
        self.worker.finished_ok.connect(self._downloaded)
        self.worker.start()

    def _progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.status.setText(f"Downloading the update… {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _failed(self, text: str) -> None:
        self.bar.hide()
        self.status.setText(text)
        self.btn_go.setEnabled(True)
        self.btn_skip.setEnabled(True)
        self.btn_later.setText("Later")

    def _downloaded(self, path: str) -> None:
        self.status.setText("Verified. Installing…")
        try:
            launch_installer(path)
        except OSError as e:
            self._failed(f"Couldn't start the installer ({e.strerror or e}).")
            return
        QTimer.singleShot(300, self.win.quit_app)          # saves the session, then exits

    def reject(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        super().reject()


class Updater(QObject):
    """Owned by the main window: automatic daily checks and Help > Check for Updates."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self._worker: CheckWorker | None = None
        self._manual = False

    @property
    def auto(self) -> bool:
        return self.win.settings.value("updates/auto", True, type=bool)

    def set_auto(self, on: bool) -> None:
        self.win.settings.setValue("updates/auto", on)

    def check_on_startup(self) -> None:
        last = float(self.win.settings.value("updates/last_check", 0) or 0)
        if self.auto and time.time() - last > CHECK_EVERY:
            QTimer.singleShot(10_000, lambda: self.check(manual=False))     # after start-up settles

    def check(self, manual: bool = True) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._manual = manual
        if manual:
            self.win.statusBar().showMessage("Checking for updates…", 4000)
        self._worker = CheckWorker(self)
        self._worker.done.connect(self._checked)
        self._worker.start()

    def _checked(self, release: Release | None, error: str) -> None:
        self.win.settings.setValue("updates/last_check", time.time())
        current = current_version()
        newer = release is not None and current is not None and release.version > current
        skipped = release is not None and self.win.settings.value("updates/skipped", "") == release.tag
        if newer and (self._manual or not skipped):
            UpdateDialog(self.win, release, current).exec()
            return
        if not self._manual:
            return                                              # automatic checks stay quiet
        name = QApplication.applicationDisplayName() or "the app"
        if error:
            QMessageBox.warning(self.win, "Check for updates", error)
        elif current is None:
            QMessageBox.information(self.win, "Check for updates",
                                    "This copy's version couldn't be determined, so it can't be compared.")
        else:
            QMessageBox.information(self.win, "Check for updates",
                                    f"You're up to date: {name} {version_text(current)} is the latest version.")
