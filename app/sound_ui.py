"""Sound page (inside the Equalizer window): louder playback and full-quality output.

Settings live in QSettings under "sound/":
  boost        allow volume up to 200% (the slider itself resets to 100% on restart)
  loudness     "make quiet songs louder" (dynaudnorm)
  exclusive    WASAPI exclusive mode (bit-perfect when nothing else processes the audio)
  hq_resample  high-quality sample-rate conversion
  channels     auto-safe | stereo | auto
"""
from __future__ import annotations

import os
import subprocess
import sys

import mutagen
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.refresh_info)

    def _set(self, key: str, value) -> None:
        self.win.settings.setValue(f"sound/{key}", value)
        self.win.apply_sound_settings()
        QTimer.singleShot(600, self.refresh_info)       # the output takes a moment to reopen

    def sync(self) -> None:
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
