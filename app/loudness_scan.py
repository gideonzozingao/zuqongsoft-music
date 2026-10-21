"""Background loudness scan: measures each song's integrated loudness (EBU R128, LUFS)
with a private, silent mpv instance running as fast as the CPU allows (~100x real time),
so every song can play at the same level without needing ReplayGain tags."""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

METER = "@m:lavfi=[ebur128=metadata=1],@sd:lavfi=[silencedetect=n=-50dB:d=0.4]"
MAX_TRIM_MS = 30_000          # longer "silences" are probably quiet music, not dead air


class LoudnessScanner(QThread):
    progress = Signal(int, int)        # measured, total
    measured = Signal(str, object)     # path, LUFS (None if it couldn't be measured)

    def __init__(self, library, parent=None) -> None:
        super().__init__(parent)
        self.library = library

    def run(self) -> None:
        try:
            import mpv
            m = mpv.MPV(vid="no", vo="null", ao="null", ao_null_untimed="yes", idle="yes",
                        audio_display="no", keep_open="yes", input_default_bindings=False)
        except Exception:
            return
        for opt, value in (("sub-auto", "no"), ("sid", "no"), ("replaygain", "no"), ("volume", "100")):
            try:
                m[opt] = value
            except Exception:
                pass
        todo = self.library.unscanned_tracks()
        done, total = self.library.loudness_progress()
        self.progress.emit(done, total)
        try:
            for path, mtime in todo:
                if self.isInterruptionRequested():
                    break
                lufs, intro, outro = self._measure(m, path)
                if self.isInterruptionRequested():
                    break
                self.library.set_loudness(path, lufs, mtime, intro, outro)
                done += 1
                self.measured.emit(path, lufs)
                self.progress.emit(done, total)
                self.msleep(15)                 # stay polite to the music that's playing
        finally:
            try:
                m.terminate()
            except Exception:
                pass

    def _measure(self, m, path: str) -> tuple[float | None, int | None, int | None]:
        """(integrated LUFS, silent intro ms, start of trailing silence ms)."""
        first: dict = {}

        def poll_intro() -> None:               # remember the FIRST silence that ended (the intro)
            if first:
                return
            try:
                md = m._get_property("af-metadata/sd") or {}
            except Exception:
                return
            if "lavfi.silence_end" in md:
                first.update(md)

        try:
            m.command("af", "clr", "")          # recreate the meters: nothing carries over
            m.command("af", "set", METER)
            m.command("loadfile", path, "replace")
            m.pause = False                     # keep-open paused at the end of the previous song
        except Exception:
            return None, None, None
        t0 = time.time()
        # Wait until the NEW file is really playing: right after loadfile, eof-reached and the
        # metadata still describe the previous song.
        while time.time() - t0 < 5:
            if self.isInterruptionRequested():
                return None, None, None
            try:
                if m.path == path and m.eof_reached is False:
                    break
                if m.path == path and m.eof_reached and (m.time_pos or 0) < 0.5 and time.time() - t0 > 1:
                    break                       # a tiny file that finished before we looked
            except Exception:
                pass
            self.msleep(2)
        failed = False
        while time.time() - t0 < 180:
            if self.isInterruptionRequested():
                failed = True
                break
            poll_intro()
            if m.eof_reached:
                break
            if m.idle_active and time.time() - t0 > 3:
                failed = True                    # couldn't be opened
                break
            self.msleep(3)
        poll_intro()
        if failed:
            return None, None, None
        try:
            md = m._get_property("af-metadata/m") or {}
            lufs = float(md.get("lavfi.r128.I"))
            lufs = lufs if lufs > -70 else None
        except Exception:
            lufs = None
        intro = outro = None
        try:
            end, dur = float(first.get("lavfi.silence_end")), float(first.get("lavfi.silence_duration"))
            if end - dur < 0.05 and end * 1000 <= MAX_TRIM_MS:
                intro = int(end * 1000)
        except (TypeError, ValueError):
            pass
        try:
            sd = m._get_property("af-metadata/sd") or {}
            length = float(m.duration or 0)
            start = float(sd.get("lavfi.silence_start"))
            last_end = float(sd.get("lavfi.silence_end", -1))
            if start > last_end and length and (length - start) * 1000 <= MAX_TRIM_MS:
                outro = int(start * 1000)        # silence that runs to the end of the file
        except (TypeError, ValueError):
            pass
        return lufs, intro, outro
