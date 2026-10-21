# Zuqongsoft Music 1.0.0

**Released 21 October 2026** · Windows 10 and 11 (64-bit)

The first release of Zuqongsoft Music: a desktop music player for the music you own, with a studio-grade audio engine, DJ-style transitions, synced lyrics and karaoke, all in a Material 3 design that takes its colours from your album art.

**Download:** `ZuqongsoftMusic-Setup-1.0.0.exe`, below under _Assets_.

---

## Highlights

**DJ mode.** Songs blend into each other with real crossfades of 1 to 12 seconds, at constant loudness through the blend. Silent intros and outros are skipped automatically, and play, pause and skip fade smoothly instead of cutting. Tracks that run into each other on an album, like live albums and DJ mixes, stay gapless.

**Studio EQ.** A parametric equalizer you shape by dragging points on a graph that shows the real filter response. Import a correction for your exact headphones from AutoEq, compare it with the original at matched loudness, and watch a live spectrum and broadcast-standard loudness meters as you go.

**Synced lyrics and karaoke.** Lyrics scroll with the song and highlight the current line; click any line to jump there. Write or fix lyrics yourself with tap-to-sync. Karaoke mode turns the lead vocal down and changes the key without changing the tempo.

**Song Radio.** Start a radio from any song, or turn on Autoplay, and the music keeps going with songs that fit. It learns from your own listening, with no internet or account needed.

**Full-screen Now Playing.** Big artwork over a slowly moving blend of the cover's colours, with lyrics and a live spectrum. Press F11 or double-click the cover.

**Your year in music.** Minutes listened, top songs, artists and albums, and listening by month, plus smart playlists such as Most Played and Forgotten Favourites.

---

## What's included

### Your library

- Scans your music folders in the background and keeps up with new and changed files. Supports MP3, FLAC, OGG, Opus, M4A/AAC, WAV and WMA.
- A songs list with sortable columns, an album grid with cover art, and search across titles, artists and albums.
- Playlists with M3U import and export.
- Star ratings, play counts and listening history. A song counts as played after you've heard half of it or 4 minutes, the same rule Last.fm uses.
- Six smart playlists that update on their own: Most Played, Recently Added, Recently Played, Top Rated, Forgotten Favourites and Never Played.

### Playing music

- Gapless playback, shuffle, repeat, and resume where you left off when you reopen the app.
- **Up Next:** _Play next_ and _Add to queue_ from any right-click menu, with drag-to-reorder. Songs you queue stay queued when you start a new album or turn on shuffle.
- **Sleep timer:** 15 to 90 minutes, or the end of the current song, with a gentle fade-out.
- **Mini player:** a small always-on-top window you can drag anywhere. Press Ctrl+M.
- **Tray icon:** controls from the system tray, with the option to keep playing when the window is closed.

### Lyrics

- Lyrics from a `.lrc` file next to the song, from lyrics embedded in the file, or online from LRCLIB.
- A lyrics editor: import `.lrc` or `.txt` files, type or paste lyrics, and sync them by pressing F8 as each line starts. Lyrics are saved as standard `.lrc` files that other players can read.

### Equalizer and sound

- **10-band equalizer** with 13 presets, your own presets, and a live response curve.
- **Studio EQ** with up to 16 bands and:
  - band solo and A/B comparison;
  - undo/redo and four snapshots;
  - target curves and a headphone measurement overlay;
  - tools: tone tilt, stereo width and balance, compressor, multiband compressor, de-esser, bass enhancer, exciter, crossfeed and convolution.
- **Automatic EQ:** a preset that applies on its own for a genre, an album or a song.
- **Separate EQ settings for each output device,** switched automatically when the device changes.
- **Louder playback:** volume up to 200% without distortion, thanks to a built-in limiter. The volume resets to 100% at each start, and you get a one-time hearing warning on headphones.
- **Even loudness:** make quiet songs louder, or play every song in your library at the same level.
- **Full quality:** exclusive mode for bit-perfect playback, high-quality resampling, and a panel showing exactly what format reaches your speakers.
- **Speakers and surround:** stereo to 5.1 upmix, virtual surround for headphones, a 2.1 subwoofer crossover, and speaker delays.
- **Output devices:** every device is listed, including Bluetooth. Playback pauses when headphones disconnect and switches to them when they connect.

### Look and feel

- Material 3 design with 10 colour themes in light and dark, any custom colour, or _Match album art_, which recolours the whole app with each song.
- **Adapts to the window size:** the sidebar folds away on medium windows, and small windows show just the player, with the library one click away.
- **Windows integration:** the app shows up in the Windows media overlay, lock screen and Bluetooth controls, with cover art. Global hotkeys work while it's in the background.

---

## Installing

1. Download **`ZuqongsoftMusic-Setup-1.0.0.exe`**.
2. Run it. Windows may show _"Windows protected your PC"_, because this first release isn't code-signed yet. Click **More info → Run anyway**.
3. Choose whether to install just for you (no administrator rights needed) or for everyone on the PC.
4. Open **Zuqongsoft Music** from the Start Menu and add your music with **File → Add Music Folder**, or drag a folder onto the window.

To uninstall, go to **Settings → Apps → Zuqongsoft Music**. You'll be asked whether to keep your library, playlists and settings. Your music files are never changed or deleted.

---

## Good to know

- **Your music files are never modified.** Lyrics you add are saved as separate `.lrc` files, and all app data lives in its own folder.
- **Online features are optional.** Lyrics lookup (LRCLIB) and the head-model download for virtual surround are the only parts that use the internet. Online lyrics can be switched off in the Lyrics tab.
- **Stats start from now.** Your listening history, Most Played and _Your year in music_ fill up as you listen.

## Known issues

- **SmartScreen warning:** the installer isn't code-signed yet, so Windows warns about it on first run.
- **No crossfades in exclusive mode:** a device in exclusive mode can only play one stream at a time.
- **Bluetooth headsets in call mode:** a headset connected in "hands-free" mode sounds quiet and mono. Pick its stereo entry in the output menu; the hands-free entry is labelled in the app.
- **Spectrum and surround:** while the Studio tab's spectrum analyser is showing, surround output is temporarily folded to stereo.
- **Vocal reduction varies:** karaoke vocal reduction works best on songs with the vocal in the centre of the mix, and less well on others.

## System requirements

- Windows 10 or 11, 64-bit.
- About 400 MB of disk space.
- Internet only for the optional online features above.

Linux and macOS users can run the app from source; see the README.

## Verifying the download

Check that your download matches the published SHA-256 checksum:

```powershell
certutil -hashfile ZuqongsoftMusic-Setup-1.0.0.exe SHA256
```

```
SHA-256: <paste the checksum here when publishing>
```

---

## Thanks

Zuqongsoft Music is built on PySide6 (Qt), mpv and FFmpeg, mutagen, PyWinRT and Material Icons. Online lyrics come from LRCLIB, and headphone corrections from the AutoEq project. See the README for licences.
