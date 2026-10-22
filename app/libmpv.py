"""Finding and installing libmpv, the DLL behind the mpv audio engine (Windows).

Searched, in order: next to MusicPlayer.exe (and its lib folder), the bundled
_internal folder, the project's lib folder, ./lib, and the user's app-data lib
folder, which is where the in-app "Download audio engine" button installs it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import BUNDLED
from typing import Callable

DLL_NAMES = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")
REPO = "shinchiro/mpv-winbuild-cmake"          # the Windows builds mpv.io links to
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def user_lib_dir() -> Path | None:
    """Writable per-user folder (Program Files isn't writable)."""
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        return Path(base) / "lib" if base else None
    except Exception:
        return None


def search_dirs() -> list[Path]:
    dirs: list[Path] = []
    if BUNDLED:                                   # PyInstaller or Nuitka build
        exe_dir = Path(sys.executable).resolve().parent
        dirs += [exe_dir, exe_dir / "lib"]
    if getattr(sys, "_MEIPASS", None):
        dirs.append(Path(sys._MEIPASS))
    dirs.append(Path(__file__).resolve().parent.parent / "lib")
    dirs.append(Path.cwd() / "lib")
    user = user_lib_dir()
    if user is not None:
        dirs.append(user)
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(str(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def find() -> Path | None:
    for d in search_dirs():
        for name in DLL_NAMES:
            if (d / name).is_file():
                return d / name
    return None


# ---- download ---------------------------------------------------------------------
Progress = Callable[[int, int], None]


def _get(url: str, progress: Progress | None = None) -> bytes:
    headers = {"User-Agent": "music-player"}
    if os.environ.get("GITHUB_TOKEN") and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        chunks, done = [], 0
        while chunk := r.read(1 << 20):
            chunks.append(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
        return b"".join(chunks)


def _wanted(name: str) -> bool:
    return name.startswith("mpv-dev-x86_64-") and "-v3-" not in name and name.endswith(".7z")


def find_asset() -> tuple[str, str]:
    """(file name, url) of the latest libmpv archive. Falls back to the release web page
    when the GitHub API is rate-limited."""
    try:
        for a in json.loads(_get(API)).get("assets", []):
            if _wanted(a["name"]):
                return a["name"], a["browser_download_url"]
    except Exception:
        pass
    req = urllib.request.Request(f"https://github.com/{REPO}/releases/latest",
                                 headers={"User-Agent": "music-player"})
    with urllib.request.urlopen(req, timeout=60) as r:
        tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
    page = _get(f"https://github.com/{REPO}/releases/expanded_assets/{tag}").decode("utf-8", "replace")
    for href in re.findall(r'href="([^"]+/releases/download/[^"]+)"', page):
        if _wanted(href.rsplit("/", 1)[-1]):
            return href.rsplit("/", 1)[-1], ("https://github.com" + href if href.startswith("/") else href)
    raise RuntimeError(f"No libmpv archive found at https://github.com/{REPO}/releases")


def _extract(archive: Path, out: Path) -> None:
    """The archive needs a 7z reader with BCJ2 support: Windows' built-in tar or 7-Zip."""
    seven = shutil.which("7z") or next((p for p in (r"C:\Program Files\7-Zip\7z.exe",
                                                    r"C:\Program Files (x86)\7-Zip\7z.exe")
                                        if os.path.exists(p)), None)
    attempts = []
    if shutil.which("tar"):
        attempts.append(["tar", "-xf", str(archive), "-C", str(out)])
    if seven:
        attempts.append([seven, "x", "-y", f"-o{out}", str(archive)])
    errors = []
    for cmd in attempts:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
        if any(out.rglob(DLL_NAMES[0])):
            return
        errors.append(f"{Path(cmd[0]).name}: {(r.stderr or r.stdout).strip()[:300] or f'exit {r.returncode}'}")
    raise RuntimeError("Could not unpack the libmpv archive. "
                       + ("; ".join(errors) if errors else "No tar or 7-Zip found.")
                       + " Install 7-Zip, or extract libmpv-2.dll from the archive manually.")


def download(dest: Path, progress: Progress | None = None, log: Callable[[str], None] = print) -> Path:
    """Download the latest libmpv-2.dll into `dest`. Raises RuntimeError with a readable message."""
    log("Finding the latest libmpv build…")
    try:
        name, url = find_asset()
    except Exception as exc:
        raise RuntimeError(f"Could not reach GitHub to find libmpv: {exc}") from exc
    log(f"Downloading {name}…")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp, name)
        try:
            archive.write_bytes(_get(url, progress))
        except Exception as exc:
            raise RuntimeError(f"Download failed: {exc}") from exc
        out = Path(tmp, "x")
        out.mkdir()
        log("Unpacking…")
        _extract(archive, out)
        found = next(out.rglob(DLL_NAMES[0]), None)
        if found is None:
            raise RuntimeError(f"{DLL_NAMES[0]} was not in {name}.")
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / DLL_NAMES[0]
        shutil.copy2(found, target)
    log(f"Saved {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return target
