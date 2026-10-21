"""Sound page (inside the Equalizer window): louder playback and full-quality output.

Settings live in QSettings under "sound/":
  boost        allow volume up to 200% (the slider itself resets to 100% on restart)
  loudness     "make quiet songs louder" (dynaudnorm)
  exclusive    WASAPI exclusive mode (bit-perfect when nothing else processes the audio)
  hq_resample  high-quality sample-rate conversion
  channels     auto-safe | stereo | auto
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import fields

import mutagen
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from .tools import KEMAR_URL, SPEAKER_MODES, SpeakerState

CHANNELS = (("auto-safe", "Automatic"), ("stereo", "Stereo (fold surround down to 2 speakers)"),
            ("auto", "Surround: use every speaker (5.1 / 7.1)"))
FORMATS = {"u8": "8-bit", "s16": "16-bit", "s24": "24-bit", "s32": "32-bit", "s64": "64-bit",
           "float": "32-bit float", "double": "64-bit float"}
OUTPUTS = {"wasapi": "WASAPI", "pulse": "PulseAudio", "pipewire": "PipeWire", "coreaudio": "Core Audio",
           "alsa": "ALSA", "null": "no output", "pcm": "file"}


def sound_settings(settings) -> dict:
    v = lambda k, d: settings.value(f"sound/{k}", d, type=type(d))  # noqa: E731
    return {"boost": v("boost", False), "loudness": v("loudness", False), "exclusive": v("exclusive", False),
            "hq_resample": v("hq_resample", True), "channels": str(settings.value("sound/channels", "auto-safe"))}


LEVEL_TARGETS = ((-14.0, "−14 LUFS · streaming level (Spotify, YouTube)"), (-16.0, "−16 LUFS · Apple Music"),
                 (-18.0, "−18 LUFS · classic hi-fi, more headroom"), (-23.0, "−23 LUFS · broadcast (EBU R128)"))


def speaker_settings(settings) -> SpeakerState:
    try:
        d = json.loads(settings.value("sound/speaker", "") or "{}")
    except (TypeError, ValueError):
        d = {}
    names = {f.name for f in fields(SpeakerState)}
    try:
        return SpeakerState(**{k: v for k, v in d.items() if k in names})
    except TypeError:
        return SpeakerState()


class _Download(QThread):
    done = Signal(bool, str)

    def __init__(self, url: str, dest: str, parent=None) -> None:
        super().__init__(parent)
        self.url, self.dest = url, dest

    def run(self) -> None:
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "Zuqongsoft Music"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            os.makedirs(os.path.dirname(self.dest), exist_ok=True)
            with open(self.dest, "wb") as f:
                f.write(data)
            self.done.emit(True, self.dest)
        except Exception as exc:
            self.done.emit(False, str(exc))


def _fmt(fmt: str | None) -> str:
    if not fmt:
        return ""
    return FORMATS.get(fmt.rstrip("p"), fmt)


def _khz(rate) -> str:
    return f"{rate / 1000:g} kHz" if rate else ""


def _channels(params: dict) -> str:
    named = params.get("hr-channels") or ""
    n = params.get("channel-count") or 0
    return {"mono": "mono", "stereo": "stereo"}.get(named) or ({1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
                                                             .get(n, f"{n} channels" if n else ""))


_source_cache: dict[str, tuple] = {}


def source_details(path: str) -> tuple[int | None, int | None]:
    """(bits per sample, bitrate kbps) from the file itself."""
    if path not in _source_cache:
        bits = kbps = None
        try:
            info = mutagen.File(path).info
            bits = getattr(info, "bits_per_sample", None) or None
            br = getattr(info, "bitrate", 0) or 0
            kbps = round(br / 1000) if br else None
        except Exception:
            pass
        _source_cache[path] = (bits, kbps)
    return _source_cache[path]


def open_windows_enhancements() -> bool:
    """The classic Sound control panel: device > Properties > Enhancements > Loudness Equalization."""
    if sys.platform != "win32":
        return False
    try:
        subprocess.Popen(["control", "mmsys.cpl,,0"])
        return True
    except OSError:
        return False


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(objectName="card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(10)
    lay.addWidget(QLabel(title, objectName="cardTitle"))
    return card, lay


class SoundPage(QWidget):
    def __init__(self, window, switch_cls, parent=None) -> None:
        super().__init__(parent)
        self.win = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        def row(layout, text, hint, widget):
            r = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(QLabel(text))
            if hint:
                h = QLabel(hint, objectName="muted")
                h.setWordWrap(True)
                col.addWidget(h)
            r.addLayout(col, 1)
            r.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(r)

        loud, ll = _card("Louder")
        self.boost = switch_cls()
        row(ll, "Allow volume above 100% (up to 200%)",
            "Boosts quiet music in software. A limiter keeps it from distorting. The volume goes back "
            "to 100% each time the app starts.", self.boost)
        self.loudness = switch_cls()
        row(ll, "Make quiet songs louder",
            "Evens out quiet songs and quiet passages, so you don't have to keep turning the volume up. "
            "Works on every file. Great for old recordings and classical.", self.loudness)
        enh = QPushButton("Open Windows sound settings…")
        enh.setCursor(Qt.CursorShape.PointingHandCursor)
        if sys.platform == "win32":
            row(ll, "Windows Loudness Equalization",
                "A Windows feature that some sound drivers offer. In the window that opens: double-click "
                "your device, then Enhancements, then tick Loudness Equalization.", enh)
        lay.addWidget(loud)

        quality, ql = _card("Full quality")
        self.exclusive = switch_cls()
        row(ql, "Exclusive mode (bit-perfect)",
            "Sends the music straight to the sound card at its real sample rate and bit depth. While it's "
            "on, other apps can't play sound on this device, and Windows enhancements are bypassed.",
            self.exclusive)
        self.hq = switch_cls()
        row(ql, "High-quality resampling",
            "Uses a cleaner converter whenever the sample rate has to change.", self.hq)
        self.channels = QComboBox()
        for key, label in CHANNELS:
            self.channels.addItem(label, key)
        row(ql, "Speakers", "Surround music needs a 5.1 or 7.1 system.", self.channels)
        lay.addWidget(quality)

        # ---- DJ mode
        from .studio_ui import ValueSlider
        dj, dl = _card("DJ mode")
        self.dj = switch_cls()
        row(dl, "Smooth transitions between songs",
            "Blends each song into the next, and fades in and out when you play, pause or skip.", self.dj)
        self.dj_xf = ValueSlider("Crossfade", 1, 12, 1, "{:.0f} s")
        dl.addWidget(self.dj_xf)
        self.dj_trim = switch_cls()
        row(dl, "Skip silence at the start and end of songs",
            "Uses the background loudness scan to find silent intros and outros.", self.dj_trim)
        self.dj_fades = switch_cls()
        row(dl, "Fade when you play, pause or skip", "", self.dj_fades)
        self.dj_albums = switch_cls()
        row(dl, "Keep album tracks gapless",
            "Songs that run into each other on an album (live albums, DJ mixes) aren't crossfaded.", self.dj_albums)
        lay.addWidget(dj)

        # ---- same loudness for every song
        lvl, lvl_l = _card("Same loudness for every song")
        self.level = switch_cls()
        row(lvl_l, "Level my whole library",
            "Measures every song's loudness in the background, then plays each at the same level, "
            "with or without ReplayGain tags. Replaces ReplayGain while it's on.", self.level)
        self.level_target = QComboBox()
        for value, label in LEVEL_TARGETS:
            self.level_target.addItem(label, value)
        row(lvl_l, "Target level", "Louder targets need more headroom; the limiter protects against clipping.",
            self.level_target)
        srow = QHBoxLayout()
        self.level_status = QLabel("", objectName="muted")
        self.btn_scan = QPushButton("Measure now")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        srow.addWidget(self.level_status, 1)
        srow.addWidget(self.btn_scan)
        lvl_l.addLayout(srow)
        lay.addWidget(lvl)

        # ---- speakers & surround
        spk, sl = _card("Speakers & surround")
        self.spk_mode = QComboBox()
        for key, label in SPEAKER_MODES:
            self.spk_mode.addItem(label, key)
        row(sl, "Speaker setup", "", self.spk_mode)
        self.spk_note = QLabel("", objectName="muted")
        self.spk_note.setWordWrap(True)
        sl.addWidget(self.spk_note)
        self.sofa_row = QWidget()
        sr = QHBoxLayout(self.sofa_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.sofa_status = QLabel("", objectName="muted")
        self.sofa_status.setWordWrap(True)
        self.btn_sofa_dl = QPushButton("Download head model (1 MB)")
        self.btn_sofa_dl.setToolTip("The MIT KEMAR dummy-head measurements, a standard choice")
        self.btn_sofa_own = QPushButton("Use my own .sofa…")
        sr.addWidget(self.sofa_status, 1)
        sr.addWidget(self.btn_sofa_dl)
        sr.addWidget(self.btn_sofa_own)
        sl.addWidget(self.sofa_row)
        grid = QHBoxLayout()
        self.crossover = QDoubleSpinBox(decimals=0, minimum=40, maximum=200, singleStep=5, suffix=" Hz")
        self.delay_l = QDoubleSpinBox(decimals=1, minimum=0, maximum=30, singleStep=0.1, suffix=" ms")
        self.delay_r = QDoubleSpinBox(decimals=1, minimum=0, maximum=30, singleStep=0.1, suffix=" ms")
        self.delay_sub = QDoubleSpinBox(decimals=1, minimum=0, maximum=30, singleStep=0.1, suffix=" ms")
        self.spk_fields = []
        for label, w, tip in (("Crossover", self.crossover, "Bass below this goes to the subwoofer"),
                              ("Left delay", self.delay_l, "Delay the left speaker (about 2.9 ms per metre closer)"),
                              ("Right delay", self.delay_r, "Delay the right speaker"),
                              ("Sub delay", self.delay_sub, "Delay the subwoofer")):
            c = QVBoxLayout()
            c.setSpacing(2)
            lab = QLabel(label, objectName="muted")
            c.addWidget(lab)
            c.addWidget(w)
            w.setToolTip(tip)
            grid.addLayout(c)
            self.spk_fields.append((label, lab, w))
        grid.addStretch(1)
        sl.addLayout(grid)
        lay.addWidget(spk)

        info, il = _card("What's playing now")
        self.info = QLabel("", objectName="audioInfo")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        il.addWidget(self.info)
        lay.addWidget(info)
        lay.addStretch(1)

        self.boost.toggled.connect(lambda on: self._set("boost", on))
        self.loudness.toggled.connect(lambda on: self._set("loudness", on))
        self.exclusive.toggled.connect(lambda on: self._set("exclusive", on))
        self.hq.toggled.connect(lambda on: self._set("hq_resample", on))
        self.channels.activated.connect(lambda _i: self._set("channels", self.channels.currentData()))
        enh.clicked.connect(open_windows_enhancements)
        self.level.toggled.connect(lambda on: self._set("level", on))
        self.dj.toggled.connect(self._dj_toggled)
        self.dj_xf.changed.connect(lambda v: self._set_dj("crossfade", v))
        self.dj_trim.toggled.connect(lambda on: self._set_dj("trim", on))
        self.dj_fades.toggled.connect(lambda on: self._set_dj("fades", on))
        self.dj_albums.toggled.connect(lambda on: self._set_dj("keep_albums", on))
        self.level_target.activated.connect(lambda _i: self._set("level_target", self.level_target.currentData()))
        self.btn_scan.clicked.connect(self.win.start_loudness_scan)
        self.spk_mode.activated.connect(self._speaker_changed)
        for w in (self.crossover, self.delay_l, self.delay_r, self.delay_sub):
            w.valueChanged.connect(self._speaker_changed)
        self.btn_sofa_dl.clicked.connect(self._download_sofa)
        self.btn_sofa_own.clicked.connect(self._choose_sofa)
        self._dl: _Download | None = None
        self._syncing = False
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.refresh_info)

    # ---- DJ mode ------------------------------------------------------------------
    def _set_dj(self, key: str, value) -> None:
        if self._syncing:
            return
        self.win.settings.setValue(f"dj/{key}", value)
        self.win.apply_sound_settings()

    def _dj_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        self.win.act_dj.setChecked(on)          # same setting as Playback > DJ Mode
        self._refresh_dj()

    def _refresh_dj(self) -> None:
        on = self.win.settings.value("dj/enabled", False, type=bool) and self.win.engine.supports_eq
        for w in (self.dj_xf, self.dj_trim, self.dj_fades, self.dj_albums):
            w.setEnabled(on)

    # ---- speakers -----------------------------------------------------------------
    def _speaker(self) -> SpeakerState:
        return SpeakerState(mode=self.spk_mode.currentData(), crossover=self.crossover.value(),
                            delay_l=self.delay_l.value(), delay_r=self.delay_r.value(),
                            delay_sub=self.delay_sub.value(), sofa_path=self._sofa_path())

    def _sofa_path(self) -> str:
        return speaker_settings(self.win.settings).sofa_path

    def _speaker_changed(self, *_a) -> None:
        if self._syncing:
            return
        spk = self._speaker()
        st = self.win.settings
        if spk.mode in ("upmix", "sub") and st.value("sound/channels", "auto-safe") != "auto":
            st.setValue("sound/channels_before", st.value("sound/channels", "auto-safe"))
            st.setValue("sound/channels", "auto")                      # let mpv output 5.1 / 2.1
            self.channels.setCurrentIndex(max(0, self.channels.findData("auto")))
        elif spk.mode not in ("upmix", "sub") and st.contains("sound/channels_before"):
            st.setValue("sound/channels", st.value("sound/channels_before"))   # put back what you had
            st.remove("sound/channels_before")
            self.channels.setCurrentIndex(max(0, self.channels.findData(st.value("sound/channels"))))
        self.win.settings.setValue("sound/speaker", json.dumps(spk.__dict__))
        self.win.apply_sound_settings()
        self._refresh_speaker_ui()
        QTimer.singleShot(700, self.refresh_info)

    def _refresh_speaker_ui(self) -> None:
        mode = self.spk_mode.currentData()
        notes = {"off": "Speaker delays line up the sound when you don't sit in the middle.",
                 "upmix": "Spreads stereo music over 5.1 speakers. Needs a 5.1 system; Speakers above is set to "
                          "surround for you.",
                 "virtual": "Makes headphones sound like speakers around you, using measurements of a real head.",
                 "sub": "Sends bass below the crossover to a subwoofer (2.1). Needs a 2.1 or surround system."}
        self.spk_note.setText(notes.get(mode, ""))
        self.sofa_row.setVisible(mode == "virtual")
        path = self._sofa_path()
        self.sofa_status.setText(f"Using {os.path.basename(path)}" if path and os.path.isfile(path)
                                 else "A head model (.sofa file) is needed.")
        for label, lab, w in self.spk_fields:
            show = (label != "Crossover" and label != "Sub delay") or mode == "sub"
            show = show and mode != "upmix" and mode != "virtual"
            lab.setVisible(show)
            w.setVisible(show)

    def _store_sofa(self, path: str) -> None:
        spk = self._speaker()
        spk.sofa_path = path
        self.win.settings.setValue("sound/speaker", json.dumps(spk.__dict__))
        self.win.apply_sound_settings()
        self._refresh_speaker_ui()

    def _download_sofa(self) -> None:
        dest = os.path.join(os.path.dirname(self.win.library.db_path), "hrtf", "MIT_KEMAR_normal_pinna.sofa")
        self.btn_sofa_dl.setEnabled(False)
        self.sofa_status.setText("Downloading…")
        self._dl = _Download(KEMAR_URL, dest, self)
        self._dl.done.connect(self._sofa_downloaded)
        self._dl.start()

    def _sofa_downloaded(self, ok: bool, result: str) -> None:
        self.btn_sofa_dl.setEnabled(True)
        if ok:
            self._store_sofa(result)
        else:
            self.sofa_status.setText(f"Download failed: {result}")

    def _choose_sofa(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Head model", os.path.expanduser("~"),
                                              "SOFA head model (*.sofa);;All files (*)")
        if path:
            self._store_sofa(path)

    def _set(self, key: str, value) -> None:
        self.win.settings.setValue(f"sound/{key}", value)
        self.win.apply_sound_settings()
        QTimer.singleShot(600, self.refresh_info)       # the output takes a moment to reopen

    def sync(self) -> None:
        self._syncing = True
        spk = speaker_settings(self.win.settings)
        self.spk_mode.setCurrentIndex(max(0, self.spk_mode.findData(spk.mode)))
        for w, v in ((self.crossover, spk.crossover), (self.delay_l, spk.delay_l), (self.delay_r, spk.delay_r),
                     (self.delay_sub, spk.delay_sub)):
            w.setValue(v)
        self.level.blockSignals(True)
        self.level.setChecked(self.win.settings.value("sound/level", False, type=bool))
        self.level.blockSignals(False)
        st = self.win.settings
        for w, key, default in ((self.dj, "dj/enabled", False), (self.dj_trim, "dj/trim", True),
                                (self.dj_fades, "dj/fades", True), (self.dj_albums, "dj/keep_albums", True)):
            w.blockSignals(True)
            w.setChecked(st.value(key, default, type=bool))
            w.blockSignals(False)
            w.setEnabled(self.win.engine.supports_eq)
        self.dj_xf.set(float(st.value("dj/crossfade", 6)))
        target = float(self.win.settings.value("sound/level_target", -14.0))
        self.level_target.setCurrentIndex(max(0, self.level_target.findData(target)))
        supported_all = self.win.engine.supports_eq
        for w in (self.level, self.level_target, self.btn_scan, self.spk_mode, self.crossover, self.delay_l,
                  self.delay_r, self.delay_sub, self.btn_sofa_dl, self.btn_sofa_own):
            w.setEnabled(supported_all)
        self._syncing = False
        self._refresh_speaker_ui()
        self._refresh_dj()
        s = sound_settings(self.win.settings)
        supported = self.win.engine.supports_eq
        for sw, key in ((self.boost, "boost"), (self.loudness, "loudness"), (self.exclusive, "exclusive"),
                        (self.hq, "hq_resample")):
            sw.blockSignals(True)
            sw.setChecked(s[key])
            sw.blockSignals(False)
            sw.setEnabled(supported)
        self.exclusive.setEnabled(supported and sys.platform == "win32")
        idx = self.channels.findData(s["channels"])
        self.channels.setCurrentIndex(max(0, idx))
        self.channels.setEnabled(supported)
        self.refresh_info()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh_info()
        self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def refresh_info(self) -> None:
        if not self.win.loudness_status:
            done, total = self.win.library.loudness_progress()
            self.win.loudness_status = (f"Measured {done:,} of {total:,} songs" if done < total
                                        else f"All {total:,} songs measured") if total else ""
        self.level_status.setText(self.win.loudness_status)
        engine = self.win.engine
        if not engine.supports_eq:
            self.info.setText("These settings need the mpv audio engine (see the Equalizer tab).")
            return
        info = engine.audio_info()
        track = engine.current_track
        if not info or not track:
            self.info.setText("Play a song to see its format and how it reaches your speakers.")
            return
        src, out = info["in"], info["out"]
        bits, kbps = source_details(track["path"])
        ext = os.path.splitext(track["path"])[1].lstrip(".").upper()
        codec = (info["codec"] or ext).upper()
        if codec.startswith("PCM"):                    # e.g. pcm_s16le inside a WAV file
            codec = f"{ext} (uncompressed)"
        source = " · ".join(x for x in (codec, f"{bits}-bit" if bits else (f"{kbps} kbps" if kbps else ""),
                                        _khz(src.get("samplerate")), _channels(src)) if x)
        mode = "exclusive" if info["exclusive"] else "shared"
        ao = OUTPUTS.get(info["ao"], info["ao"].upper())
        output = " · ".join(x for x in (_fmt(out.get("format")), _khz(out.get("samplerate")), _channels(out),
                                        f"{ao} {mode}" if info["ao"] == "wasapi" else ao) if x)
        notes = list(info["processing"])
        if src.get("samplerate") and out.get("samplerate") and src["samplerate"] != out["samplerate"]:
            notes.append(f"resampled {_khz(src['samplerate'])} → {_khz(out['samplerate'])}")
        if src.get("channel-count") and out.get("channel-count") and src["channel-count"] != out["channel-count"]:
            notes.append(f"{_channels(src)} → {_channels(out)}")
        out_fmt = (out.get("format") or "").rstrip("p")
        int_bits = {"s16": 16, "s24": 24, "s32": 32}.get(out_fmt, 0)
        bit_perfect = (info["exclusive"] and not notes and int_bits
                       and (bits is None or int_bits >= bits))
        if bit_perfect:
            path = "<b>✓ Bit-perfect</b>: the sound card gets exactly what's in the file."
        elif not info["exclusive"] and info["ao"] == "wasapi":
            path = ("Shared mode: Windows mixes this with other apps and converts it to the device's format."
                    + (f"<br>Also: {', '.join(notes)}." if notes else ""))
        else:
            path = ("Processing: " + ", ".join(notes) + ".") if notes else "Played without changes."
        self.info.setText(f"<b>Source</b>&nbsp; {source}<br><b>Output</b>&nbsp; {output or 'starting…'}<br><br>{path}")
