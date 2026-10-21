# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm --clean MusicPlayer.spec
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ONEFILE = False   # True = a single ZuqongsoftMusic.exe (starts slower, more antivirus false positives)
NAME = "ZuqongsoftMusic"

datas = [("assets/icon.png", "assets")]
# libmpv for the mpv engine (equalizer, gapless). Without it the app falls back to Qt playback.
binaries = [(str(p), ".") for p in Path("lib").glob("*mpv*.dll")]
if not binaries and sys.platform == "win32":
    print("WARNING: lib/libmpv-2.dll not found - building without the equalizer engine.")
hiddenimports = []

# The Windows media overlay is imported inside a worker thread; bundle the WinRT
# projection explicitly so it's never missed.
if sys.platform == "win32":
    try:
        d, b, h = collect_all("winrt")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        print(f"winrt not installed, building without the media overlay: {exc}")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

common = dict(
    name=NAME,
    console=False,
    upx=False,            # UPX breaks Qt DLLs and triggers antivirus
    icon="assets/icon.ico",
    version="version_info.txt" if sys.platform == "win32" else None,
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
