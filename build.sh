#!/usr/bin/env bash
# Linux / macOS build. PyInstaller can't cross-compile: run build.bat on Windows for a .exe.
# Usage: ./build.sh
set -euo pipefail

APPNAME="GaramutMusic"          # must match NAME in MusicPlayer.spec

cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
DIST="$ROOT/dist"
OUT="$DIST/$APPNAME/$APPNAME"

fail() { echo; echo "BUILD FAILED: $*" >&2; exit 1; }

echo "Project folder: $ROOT"
[ -f MusicPlayer.spec ] || fail "MusicPlayer.spec not found. Put build.sh next to MusicPlayer.spec and main.py."

# Every file the build needs; list missing ones instead of building a broken app.
missing=()
for f in main.py requirements.txt app/ui.py app/engine.py app/libmpv.py app/equalizer.py \
         app/equalizer_ui.py app/audio_devices.py app/theme_picker.py app/lyrics.py app/lyrics_editor.py app/panels.py \
         app/stats_page.py app/play_tracker.py app/responsive.py app/sound_ui.py app/studio.py app/studio_ui.py app/tools.py app/loudness_scan.py app/radio.py app/fullscreen.py app/mini.py assets/icon.ico assets/icon.png; do
    [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo; echo "These project files are missing:"; printf '  %s\n' "${missing[@]}"
    fail "copy them from the latest version of the project, then run build.sh again."
fi

# ---- 1. Virtual environment (recreated if missing or broken) ----
if [ ! -x "$PY" ]; then
    echo; echo "Creating virtual environment..."
    rm -rf .venv
    python3 -m venv .venv || fail "could not create a virtual environment. Install Python 3.10-3.13 (and python3-venv on Debian/Ubuntu)."
fi
"$PY" --version

# ---- 2. Dependencies ----
# Use the venv's python directly so a global PyInstaller can never be picked up by mistake.
echo; echo "Installing dependencies..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt "pyinstaller>=6.10"

# ---- 3. libmpv (system library here; optional, the app falls back without it) ----
if "$PY" -c "import mpv" >/dev/null 2>&1; then
    echo "libmpv found: the equalizer and gapless playback will work."
else
    echo
    echo "WARNING: libmpv not found. The app will build, but without the equalizer."
    echo "  Ubuntu/Debian: sudo apt install libmpv2      Fedora: sudo dnf install mpv-libs"
    echo "  macOS:         brew install mpv"
fi

# Qt needs libxcb-cursor to open windows on X11 (Linux only).
if [ "$(uname -s)" = "Linux" ] && command -v ldconfig >/dev/null && ! ldconfig -p | grep -q libxcb-cursor; then
    echo
    echo "WARNING: libxcb-cursor is missing; the app won't open on X11 desktops without it."
    echo "  Ubuntu/Debian: sudo apt install libxcb-cursor0      Fedora: sudo dnf install xcb-util-cursor"
fi

# ---- 4. Build ----
# A running copy of the app can hold files open in dist/.
pkill -x "$APPNAME" 2>/dev/null || true
echo; echo "Building..."
"$PY" -m PyInstaller --noconfirm --clean --distpath "$DIST" --workpath "$ROOT/build" MusicPlayer.spec

# ---- 5. Verify ----
[ -x "$OUT" ] || fail "$OUT was not created. Check that APPNAME in build.sh matches NAME in MusicPlayer.spec."
echo
echo "Build succeeded:"
echo "  $OUT"
echo "Share the whole $DIST/$APPNAME folder (e.g. tar -czf $APPNAME.tar.gz -C dist $APPNAME)."
echo "Note: libmpv isn't bundled on Linux/macOS; other machines need it installed for the equalizer."
