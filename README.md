<p align="center">
  <img src="assets/icon.png" width="128" alt="Zuqongsoft Music icon">
</p>

<h1 align="center">Zuqongsoft Music</h1>

<p align="center">
  A Material 3 desktop music player for your own library, with a studio-grade audio engine,<br>
  DJ crossfades, synced lyrics, karaoke and listening stats. Built with PySide6 and mpv.
</p>

---

## Highlights

- **DJ mode**: real crossfades between songs, silent intros and outros skipped, smooth fades on play, pause and skip
- **Studio EQ**: parametric equalizer with AutoEq headphone correction, A/B comparison, live spectrum and loudness meters
- **Synced lyrics and karaoke**: lyrics that follow the song, a lyrics editor with tap-to-sync, vocal reduction and key change
- **Song Radio**: endless playlists of songs that fit, learned from your own listening
- **Full-screen Now Playing**: big artwork, moving colours from the cover, lyrics and spectrum
- **Your year in music**: plays, top songs, artists and albums, smart playlists
- **Theme from album art**: the whole app recolours from the cover of each song

---

## Features

### Library

- Background, incremental scanning of your music folders (MP3, FLAC, OGG, Opus, M4A/AAC, WAV, WMA)
- Songs list with sortable columns (artist → album → track by default), album grid with cover art, search across title, artist and album
- Playlists with M3U import and export; drag a music folder onto the window to add it

### Playback

- mpv audio engine with gapless playback; automatic fallback to Qt Multimedia if the engine isn't installed
- **Up Next** queue: _Play next_ and _Add to queue_ from any right-click menu, drag to reorder
- **Song Radio** (_right-click a song → Start radio from this song_) and **Autoplay**, which keeps adding songs that fit when the queue runs low
- **DJ mode** (Ctrl+D): crossfades of 1–12 s with constant loudness through the blend, silent intros and outros skipped, smooth fades on play, pause and skip; consecutive album tracks stay gapless
- Shuffle, repeat all/one, and resume where you left off on the next launch
- **Sleep timer**: 15–90 minutes or the end of the song, with a gentle fade-out

### Lyrics and karaoke

- Karaoke-style synced lyrics; click a line to jump there
- Sources: an `.lrc` file next to the song, lyrics embedded in the file, or [LRCLIB](https://lrclib.net) online (cached)
- **Lyrics editor**: import `.lrc`/`.txt`, type or paste, tap-to-sync with F8, nudge timing, save as `.lrc`
- **Karaoke** (Ctrl+K): lead-vocal reduction that keeps bass and stereo instruments, key change of ±6 semitones without changing the tempo, and full-screen karaoke lyrics

### Equalizer and Studio

- **10-band equalizer** with pre-amp, 13 presets, your own presets and a live response curve; import and export as a Frequency (Hz) / Gain (dB) table
- **Studio EQ** (Ctrl+Alt+E): up to 16 parametric bands (peak, shelves, cuts, notch) that you drag on a graph showing the real filter response
  - AutoEq / Equalizer APO / REW import and export
  - A/B comparison at matched loudness, band solo, undo/redo and snapshots A–D
  - Live spectrum analyser, LUFS loudness meter, left/right level and stereo correlation meters
  - Target curves (Harman over-ear/in-ear, warm, bright, or your own CSV) and headphone measurement overlay
  - Tools: tone tilt, stereo width/balance/mono/swap/polarity, compressor, multiband compressor, de-esser, bass enhancer, exciter, crossfeed, convolution (impulse responses)
- **Automatic EQ**: a preset per genre, album or song (right-click a song → Automatic EQ)
- Separate EQ settings for each output device, switched automatically

### Sound

- **Volume boost** up to 200% with a limiter that prevents distortion; a one-time hearing warning on headphones
- **Make quiet songs louder** (dynamic levelling) and **same loudness for every song** (EBU R128 library scan, about 150× real time)
- **Exclusive mode** (bit-perfect output on Windows), high-quality resampling, and a "What's playing now" panel showing the real source and output format
- **Speakers and surround**: stereo → 5.1 upmix, virtual surround for headphones (one-click head-model download), 2.1 subwoofer crossover, speaker delays
- Output device picker with Bluetooth detection; pauses when headphones disconnect and switches when they connect

### Stats and playlists

- Play counts, skips, star ratings and listening history (a play counts after half the song or 4 minutes, like Last.fm)
- Smart playlists: Most Played, Recently Added, Recently Played, Top Rated, Forgotten Favourites, Never Played
- **Your year in music** (Ctrl+Y): minutes listened, top songs, artists and albums, listening by month

### Look and feel

- Material 3 design, generated from a seed colour: 10 themes in light and dark, any custom colour, follow the system, or **match the album art**
- Responsive layout: the sidebar folds away on medium windows; small windows show just the player, with the library one click away
- **Full-screen Now Playing** (F11), **mini player** (Ctrl+M) and a **tray icon**, with the option to keep playing in the tray when the window is closed

### Windows integration

- Windows media overlay (lock screen, volume flyout, Bluetooth buttons) with cover art
- Global hotkeys that work while the app is in the background, plus the keyboard's media keys

---

## Requirements

|              |                                                                           |
| ------------ | ------------------------------------------------------------------------- |
| **OS**       | Windows 10 or 11, 64-bit (Linux and macOS can run from source, see below) |
| **Python**   | 3.10 to 3.13, **64-bit** (python.org or Microsoft Store)                  |
| **Internet** | Needed once, to download the audio engine (about 32 MB)                   |

---

## Run from source

All commands run from the project folder.

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

This saves `lib\libmpv-2.dll`. You can skip this step: the app still plays with the basic engine, and the Equalizer window has a **Download audio engine** button.

**3. Start the app**

```powershell
python main.py
```

Add your music with **File → Add Music Folder** (Ctrl+O), or drag a folder onto the window.

---

## Build the Windows app (.exe)

Double-click **`build.bat`**, or run it from a terminal. It runs these steps:

1. Checks that every project file it needs is present, and lists any that are missing
2. Creates `.venv` (or repairs a broken one) and installs the dependencies and PyInstaller
3. Downloads `lib\libmpv-2.dll` if it isn't there yet
4. Builds the app with `MusicPlayer.spec`
5. Confirms the `.exe` was created

The result:

```
dist\ZuqongsoftMusic\ZuqongsoftMusic.exe
```

**To share it, zip the whole `dist\ZuqongsoftMusic` folder.** The `.exe` is a small launcher; the `_internal` folder next to it holds Python, Qt, FFmpeg and libmpv.

| Option                                  | How                                                                                       |
| --------------------------------------- | ----------------------------------------------------------------------------------------- |
| App or file name                        | `NAME` in `MusicPlayer.spec` and `APPNAME` at the top of `build.bat` (keep them the same) |
| Name shown by Windows, version, company | `version_info.txt`                                                                        |
| Single `.exe` instead of a folder       | `ONEFILE = True` in `MusicPlayer.spec` (starts slower, more antivirus false alarms)       |
| No pause at the end (for CI)            | `build.bat /nopause`                                                                      |
| Save the build output                   | `build.bat /nopause > build.log 2>&1`                                                     |

The `.exe` isn't code-signed, so other people will see a SmartScreen warning the first time (**More info → Run anyway**).

---

## Linux and macOS

Run from source. The Windows media overlay, global hotkeys, exclusive mode and Bluetooth registry detection are Windows-only; everything else works.

```bash
# Ubuntu / Debian            # macOS
sudo apt install libmpv2     # brew install mpv
sudo apt install libxcb-cursor0          # Linux: needed by Qt on X11

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

`bash build.sh` builds a native app the same way `build.bat` does; PyInstaller only builds for the system it runs on.

---

## Using the app

### Keyboard shortcuts

| Shortcut        | Action                          | Shortcut     | Action                |
| --------------- | ------------------------------- | ------------ | --------------------- |
| Ctrl+Space      | Play / pause                    | Ctrl+E       | Equalizer             |
| Ctrl+→ / Ctrl+← | Next / previous                 | Ctrl+Alt+E   | Studio EQ             |
| Ctrl+S          | Shuffle                         | Ctrl+Shift+E | Sound settings        |
| Ctrl+R          | Cycle repeat                    | Ctrl+T       | Themes and appearance |
| Ctrl+D          | DJ mode                         | Ctrl+K       | Karaoke               |
| Ctrl+F          | Search                          | F11          | Full screen           |
| Ctrl+1 / Ctrl+2 | Songs / Albums                  | Ctrl+M       | Mini player           |
| Ctrl+Y          | Your year in music              | Ctrl+B       | Show/hide sidebar     |
| Ctrl+P / L / U  | Playing / Lyrics / Up Next tab  | Ctrl+N       | New playlist          |
| Ctrl+O          | Add music folder                | F5           | Rescan library        |
| Delete          | Remove from playlist or Up Next | Ctrl+Q       | Quit                  |

**Full screen:** Space play/pause · ←/→ seek 5 s · N / P next/previous · ↑/↓ volume · K karaoke · L lyrics · Esc exit
**Lyrics editor:** F8 (or Ctrl+Enter) stamp the line · Ctrl+S save
**Studio graph:** drag a point to move it · scroll on it for width · double-click to add a band · right-click for type/off/delete · hold Ctrl and press a point to solo it · Ctrl+Z / Ctrl+Y undo/redo
**Global hotkeys (Windows):** Ctrl+Alt+Space play/pause · Ctrl+Alt+→/← next/previous · Ctrl+Alt+↑/↓ volume

### Adding lyrics

Open the **Lyrics** tab and use **Add lyrics** or **Import file…** (or the ⋮ menu). To sync, play the song and press **F8** as each line starts. Lyrics are saved as an `.lrc` file named like the song, next to it:

```
Music\Artist\Album\01 Song Title.mp3
Music\Artist\Album\01 Song Title.lrc
```

### Correcting your headphones (AutoEq)

1. Go to [autoeq.app](https://autoeq.app), pick your headphones, and download **ParametricEQ.txt** (16 filters or fewer).
2. In the app: **Studio → Import AutoEq…**, then use **Compare A/B** to hear the difference.

For speakers, [spinorama.org](https://spinorama.org) has EQ files for many models, or measure your room with REW and import its Equalizer APO export.

### DJ mode

Turn it on with **Ctrl+D**, and adjust it in **Sound → DJ mode**. Skipping silence uses the background loudness scan, which runs automatically. Crossfades are disabled in exclusive mode, because an exclusive device can only play one stream.

---

## Where your data is stored

| What                                                                                            | Location                                                                  |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Library, playlists, stats, lyrics cache, loudness data                                          | `%APPDATA%\Zuqongtech\MusicPlayer\library.db`                             |
| Audio engine downloaded in the app, head model, impulse responses, lyrics for read-only folders | `%APPDATA%\Zuqongtech\MusicPlayer\` (`lib`, `hrtf`, `impulses`, `lyrics`) |
| Settings (folders, themes, EQ, sound, DJ, window)                                               | Registry: `HKEY_CURRENT_USER\Software\Zuqongtech\MusicPlayer`             |

Your music files are never modified (lyrics are saved as separate `.lrc` files). Rebuilding the app keeps all of the above. To start fresh, close the app and delete that folder and registry key.

---

## Troubleshooting

**The Equalizer says the mpv audio engine isn't available**
The message lists every folder it searched. Click **Download audio engine** in the Equalizer window, run `python tools\get_libmpv.py` and rebuild, or put `libmpv-2.dll` next to `ZuqongsoftMusic.exe`.

**`get_libmpv.py` can't unpack the archive**
It needs Windows' built-in `tar` (Windows 10 1803 or later) or [7-Zip](https://www.7-zip.org). As a last resort, download `mpv-dev-x86_64-*.7z` from [shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake/releases) and put `libmpv-2.dll` in `lib\`. If GitHub rate-limits the download, set `GITHUB_TOKEN` first.

**`build.bat` says project files are missing, or the `.exe` is missing after a build**
Copy the listed files from the latest version of the project. If the build succeeded but the `.exe` is gone, antivirus quarantined it: restore it in _Windows Security → Protection history_ and exclude the `dist` folder. Also check that `APPNAME` in `build.bat` matches `NAME` in the spec.

**Sound is quiet on Bluetooth headphones**
Pick the stereo entry in the output menu, not the one marked _(hands-free, low quality)_. Also set Windows' Communications tab to _Do nothing_ (Win+R → `mmsys.cpl`).

**Virtual surround is silent**
It needs a head model: click **Download head model** in Sound → Speakers & surround.

**No lyrics for a song**
LRCLIB needs correct artist and title tags. Use **⋮ → Search again**, or add your own with **Add lyrics**.

**Force the basic audio engine**
Set `MUSICPLAYER_ENGINE=qt` before starting the app, to check whether a problem comes from mpv.

---

## Project structure

```
musicplayer/
├─ main.py                Entry point
├─ requirements.txt       Python dependencies
├─ MusicPlayer.spec       PyInstaller build recipe
├─ version_info.txt       Windows file properties for the .exe
├─ build.bat / build.sh   One-step build scripts
├─ assets/                App icon (.ico, .png, SVG sources)
├─ tools/
│  ├─ get_libmpv.py       Downloads libmpv-2.dll into lib/
│  └─ make_icon.py        Regenerates the icon files
├─ lib/                   libmpv-2.dll (downloaded, not committed)
└─ app/
   ├─ ui.py               Main window and how everything connects
   ├─ engine.py           Playback engines (mpv with two decks for crossfades; Qt fallback) and the queue
   ├─ library.py          SQLite library, scanner, playlists, stats, loudness data
   ├─ models.py           Song table, album grid, cover cache, star ratings
   ├─ responsive.py       Animated wide / medium / compact layouts
   ├─ panels.py           Lyrics view, Up Next and rating widgets
   ├─ lyrics.py           .lrc / embedded / LRCLIB lyrics and .lrc saving
   ├─ lyrics_editor.py    Lyrics editor with tap-to-sync
   ├─ play_tracker.py     Counts plays and skips
   ├─ stats_page.py       "Your year in music"
   ├─ radio.py            Song Radio and Autoplay
   ├─ loudness_scan.py    Background loudness and silence scan
   ├─ fullscreen.py       Full-screen Now Playing and karaoke controls
   ├─ mini.py             Mini player, tray icon and sleep timer
   ├─ equalizer.py        EQ model, presets and the complete filter chain
   ├─ equalizer_ui.py     Equalizer window (Equalizer / Studio / Sound tabs)
   ├─ studio.py           Parametric EQ maths and AutoEq import/export
   ├─ studio_ui.py        Studio tab: graph, meters and tools
   ├─ tools.py            Studio tools, speaker routing, analyser, target curves
   ├─ sound_ui.py         Sound tab: boost, DJ mode, levelling, speakers
   ├─ theme.py            Material 3 colour schemes, style sheet, icons
   ├─ theme_picker.py     Appearance window and theme menu
   ├─ audio_devices.py    Output devices and Bluetooth detection
   ├─ system_media.py     Windows media overlay and global hotkeys
   └─ libmpv.py           Finds and downloads libmpv
```

---

## Third-party components

| Component                                                                         | Used for                                 | Licence                      |
| --------------------------------------------------------------------------------- | ---------------------------------------- | ---------------------------- |
| [PySide6 / Qt](https://doc.qt.io/qtforpython-6/)                                  | User interface                           | LGPL-3.0                     |
| [mpv / libmpv](https://mpv.io) with FFmpeg                                        | Audio engine, filters                    | GPL-2.0+ (LGPL builds exist) |
| [python-mpv](https://github.com/jaseg/python-mpv)                                 | Python bindings for libmpv               | GPL-2.0+ or LGPL-2.1+        |
| [mutagen](https://github.com/quodlibet/mutagen)                                   | Tags and cover art                       | GPL-2.0+                     |
| [PyWinRT](https://github.com/pywinrt/pywinrt)                                     | Windows media overlay                    | MIT                          |
| [Material Icons](https://fonts.google.com/icons)                                  | Icons                                    | Apache-2.0                   |
| [LRCLIB](https://lrclib.net)                                                      | Online lyrics                            | Free public API              |
| MIT KEMAR head measurements (via [libmysofa](https://github.com/hoene/libmysofa)) | Virtual surround (downloaded on request) | See the libmysofa repository |

Several of these are GPL-licensed, which affects how you may distribute the app. Check each licence before publishing builds.

---

## Licence

© 2026 Zuqongtech. Add your licence here.
