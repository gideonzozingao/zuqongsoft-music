<p align="center">
  <img src="assets/icon.png" width="128" alt="Music app icon">
</p>

<h1 align="center">Zuqongsoft Music</h1>

<p align="center">
  A Material 3 desktop music player for your local library, with gapless playback,<br>
  a 10-band equalizer, synced lyrics and listening stats. Built with PySide6 and mpv.
</p>

---

## Features

**Library**

- Scans your music folders in the background and picks up new and changed files incrementally
- Songs list with sortable columns (artist → album → track by default), an album grid with cover art, and search across title, artist and album
- Reads MP3, FLAC, OGG, Opus, M4A/AAC, WAV and WMA tags; cover art from the file or a `cover.jpg` / `folder.jpg` next to it

**Playback**

- Gapless playback through the mpv audio engine, with an automatic fallback to Qt Multimedia
- **Up Next** queue: _Play next_ / _Add to queue_ from any right-click menu, drag to reorder
- Shuffle, repeat all / one, and resume where you left off when you reopen the app
- **10-band equalizer** with pre-amp, 13 presets, your own presets, a live response curve and per-device profiles
- ReplayGain volume levelling

**Lyrics**

- Karaoke-style synced lyrics that scroll with the song; click a line to jump there
- Loaded from a `.lrc` file next to the song, lyrics embedded in the file, or [LRCLIB](https://lrclib.net) online

**Playlists and stats**

- Playlists with M3U import and export
- Star ratings, play counts, skips and listening history
- Smart playlists: Most Played, Recently Added, Recently Played, Top Rated, Forgotten Favourites, Never Played
- **Your year in music**: minutes listened, top songs, artists and albums, and listening by month

**Look and feel**

- Material 3 design with 10 colour themes in light and dark, any custom colour, or **Match album art**, which recolours the app from each song's cover
- Follows Windows light/dark mode if you want it to

**Windows integration**

- Shows in the Windows media overlay, lock screen and quick settings, with cover art
- Global hotkeys that work while the app is in the background
- Lists every output device (speakers, USB, HDMI, Bluetooth); pauses when headphones disconnect and switches when they connect

---

## Requirements

|              |                                                                           |
| ------------ | ------------------------------------------------------------------------- |
| **OS**       | Windows 10 or 11, 64-bit (Linux and macOS can run from source, see below) |
| **Python**   | 3.10 to 3.13, **64-bit** (python.org or Microsoft Store)                  |
| **Internet** | Needed once, to download the audio engine (about 32 MB)                   |

---

## Run from source

Use this while developing. All commands run from the project folder.

**1. Create a virtual environment and install the dependencies**

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> If `py` isn't found, use `python` instead. In PowerShell, if activation is blocked, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**2. Download the audio engine (libmpv)**

```powershell
python tools\get_libmpv.py
```

This saves `lib\libmpv-2.dll`. You can skip this step: the app still plays music with the basic engine, and the Equalizer window has a **Download audio engine** button that does the same thing.

**3. Start the app**

```powershell
python main.py
```

On first launch, add your music with **File → Add Music Folder** (`Ctrl+O`), or drag a folder onto the window.

---

## Build the Windows app (.exe)

Double-click **`build.bat`**, or run it from a terminal:

```powershell
build.bat
```

It runs these steps:

1. Checks that every project file it needs is present, and lists any that are missing
2. Creates `.venv` (or repairs a broken one) and installs the dependencies and PyInstaller
3. Downloads `lib\libmpv-2.dll` if it isn't there yet
4. Builds the app with PyInstaller using `MusicPlayer.spec`
5. Confirms the `.exe` was created

The finished app is in:

```
dist\MusicPlayer\MusicPlayer.exe
```

**To share it, zip the whole `dist\MusicPlayer` folder.** The `.exe` is a small launcher; the `_internal` folder next to it holds Python, Qt, FFmpeg and libmpv, and the app can't run without it.

Build options:

| What                                | How                                                                                          |
| ----------------------------------- | -------------------------------------------------------------------------------------------- |
| Single `.exe` instead of a folder   | Set `ONEFILE = True` in `MusicPlayer.spec`. Starts slower and triggers antivirus more often. |
| Rename the `_internal` folder       | Add `contents_directory="lib"` to `common` in `MusicPlayer.spec`                             |
| Version number and file description | Edit `version_info.txt`                                                                      |
| No pause at the end (for CI)        | `build.bat /nopause`                                                                         |
| Save the build output               | `build.bat /nopause > build.log 2>&1`                                                        |

The `.exe` isn't code-signed, so other people will see a SmartScreen warning the first time (**More info → Run anyway**).

---

## Linux and macOS

These platforms can run the app from source. The Windows media overlay, global hotkeys and Bluetooth detection are Windows-only; everything else works.

```bash
# Ubuntu / Debian
sudo apt install libmpv2
# macOS
brew install mpv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

`build.sh` builds a native app the same way `build.bat` does. PyInstaller can only build for the system it runs on.

---

## Using the app

### Keyboard shortcuts

| Shortcut                       | Action                                        |
| ------------------------------ | --------------------------------------------- |
| `Ctrl+Space`                   | Play / pause                                  |
| `Ctrl+→` / `Ctrl+←`            | Next / previous                               |
| `Ctrl+S`                       | Shuffle on/off                                |
| `Ctrl+R`                       | Cycle repeat (off / all / one)                |
| `Ctrl+F`                       | Search                                        |
| `Ctrl+1` / `Ctrl+2`            | Songs / Albums                                |
| `Ctrl+Y`                       | Your year in music                            |
| `Ctrl+P` / `Ctrl+L` / `Ctrl+U` | Now Playing / Lyrics / Up Next tab            |
| `Ctrl+E`                       | Equalizer                                     |
| `Ctrl+T`                       | Appearance (themes)                           |
| `Ctrl+O`                       | Add music folder                              |
| `F5`                           | Rescan library                                |
| `Ctrl+N`                       | New playlist                                  |
| `Delete`                       | Remove from the open playlist or from Up Next |
| `Ctrl+Q`                       | Quit                                          |

**Global hotkeys** (work in the background; turn off under **Playback → Global Hotkeys**):

| Shortcut                    | Action           |
| --------------------------- | ---------------- |
| `Ctrl+Alt+Space`            | Play / pause     |
| `Ctrl+Alt+→` / `Ctrl+Alt+←` | Next / previous  |
| `Ctrl+Alt+↑` / `Ctrl+Alt+↓` | Volume up / down |

The keyboard's media keys also work, through the Windows media overlay.

### Adding your own lyrics

Save a `.lrc` file with the same name as the song, in the same folder:

```
Music\Artist\Album\01 Song Title.mp3
Music\Artist\Album\01 Song Title.lrc
```

Local `.lrc` files always win over online results. Synced lines look like `[01:23.45]Line of lyrics`; a file without timestamps shows as plain text.

### How plays are counted

A song counts as played once you've listened to half of it or 4 minutes, whichever comes first (the same rule Last.fm uses). Listening time is measured as real time, so seeking ahead doesn't count. Leaving a song before that counts as a skip. Stats start from the day you install this version.

---

## Where your data is stored

| What                                               | Location                                                      |
| -------------------------------------------------- | ------------------------------------------------------------- |
| Library, playlists, stats, lyrics cache            | `%APPDATA%\Zuqongtech\MusicPlayer\library.db`                 |
| Audio engine downloaded from inside the app        | `%APPDATA%\Zuqongtech\MusicPlayer\lib\`                       |
| Settings (folders, theme, volume, EQ, window size) | Registry: `HKEY_CURRENT_USER\Software\Zuqongtech\MusicPlayer` |

Your music files are never modified. Rebuilding or reinstalling the app keeps all of the above. To start completely fresh, close the app, then delete that folder and registry key.

---

## Troubleshooting

**The Equalizer says the mpv audio engine isn't available**
The app couldn't find `libmpv-2.dll`; the message lists every folder it searched. Click **Download audio engine** in the Equalizer window, or run `python tools\get_libmpv.py` and rebuild. You can also place `libmpv-2.dll` next to `MusicPlayer.exe`.

**`get_libmpv.py` can't unpack the archive**
The archive needs Windows' built-in `tar` (Windows 10 1803 or later) or [7-Zip](https://www.7-zip.org). Install 7-Zip and run it again. As a last resort, download `mpv-dev-x86_64-*.7z` from [shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake/releases), extract `libmpv-2.dll`, and put it in `lib\`.

**`get_libmpv.py` reports a GitHub rate limit**
The script already falls back to the release web page. If that fails too, set a personal access token first: `set GITHUB_TOKEN=your_token`.

**`build.bat` says project files are missing**
Copy the listed files from the latest version of the project into the same folders, then run it again.

**The build succeeds but `MusicPlayer.exe` is missing**
Antivirus quarantined it. In **Windows Security → Virus & threat protection → Protection history**, restore the file and add the `dist` folder as an exclusion.

**The media overlay shows "Python" instead of "Music"**
That happens only when running from source. The built `.exe` shows its own name and icon.

**No lyrics for a song**
LRCLIB needs correct artist and title tags; songs tagged _Unknown Artist_ aren't looked up. Use **⋮ → Search again** on the Lyrics tab, or add a `.lrc` file.

**Explorer still shows the old icon after a rebuild**
Windows caches icons. Rename the `.exe`, or sign out and back in.

**Advanced: force the basic audio engine**
Set `MUSICPLAYER_ENGINE=qt` before starting the app. Useful for checking whether a playback problem comes from mpv.

---

## Project structure

```
musicplayer/
├─ main.py                 Entry point
├─ requirements.txt        Python dependencies
├─ MusicPlayer.spec        PyInstaller build recipe
├─ version_info.txt        Windows file properties for the .exe
├─ build.bat / build.sh    One-step build scripts
├─ assets/                 App icon (.ico, .png, SVG sources)
├─ tools/
│  ├─ get_libmpv.py        Downloads libmpv-2.dll into lib/
│  └─ make_icon.py         Regenerates the icon files
├─ lib/                    libmpv-2.dll (downloaded, not committed)
└─ app/
   ├─ ui.py                Main window
   ├─ engine.py            Playback engines (mpv + Qt fallback) and the queue
   ├─ library.py           SQLite library, scanner, playlists, stats, smart playlists
   ├─ models.py            Track table, album grid, cover cache, star ratings
   ├─ panels.py            Lyrics, Up Next and rating widgets
   ├─ lyrics.py            .lrc / embedded / LRCLIB lyrics
   ├─ play_tracker.py      Counts plays and skips
   ├─ stats_page.py        "Your year in music"
   ├─ equalizer.py         EQ model, presets, mpv filter chain
   ├─ equalizer_ui.py      Equalizer window
   ├─ theme.py             Material 3 colour schemes, style sheet, icons
   ├─ theme_picker.py      Appearance window and theme menu
   ├─ audio_devices.py     Output devices and Bluetooth detection
   ├─ system_media.py      Windows media overlay and global hotkeys
   └─ libmpv.py            Finds and downloads libmpv
```

---

## Third-party components

| Component                                         | Used for                   | Licence                      |
| ------------------------------------------------- | -------------------------- | ---------------------------- |
| [PySide6 / Qt](https://doc.qt.io/qtforpython-6/)  | User interface             | LGPL-3.0                     |
| [mpv / libmpv](https://mpv.io)                    | Audio engine, equalizer    | GPL-2.0+ (LGPL builds exist) |
| [python-mpv](https://github.com/jaseg/python-mpv) | Python bindings for libmpv | GPL-2.0+ or LGPL-2.1+        |
| [mutagen](https://github.com/quodlibet/mutagen)   | Reading tags and cover art | GPL-2.0+                     |
| [PyWinRT](https://github.com/pywinrt/pywinrt)     | Windows media overlay      | MIT                          |
| [Material Icons](https://fonts.google.com/icons)  | Icons                      | Apache-2.0                   |
| [LRCLIB](https://lrclib.net)                      | Online lyrics              | Free public API              |

Several of these are GPL-licensed, which affects how you may distribute the app. Check each licence before publishing builds.

---

## Licence
