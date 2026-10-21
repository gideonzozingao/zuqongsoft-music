"""Equalizer model: 10 octave bands, presets, the mpv filter chain and saved profiles.

Each band is an FFmpeg peaking filter (`equalizer`, one octave wide). A pre-amp
(`volume`) comes first and a limiter (`alimiter`) last, so boosts don't clip.
Every filter is labelled, so a slider move updates one gain live, without
rebuilding the chain.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from .studio import StudioState

BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
BAND_LABELS = ("31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
GAIN_RANGE = 12.0          # +/- dB per band
PREAMP_RANGE = 12.0

PRESETS: dict[str, tuple[float, ...]] = {
    "Flat":          (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Bass Boost":    (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
    "Treble Boost":  (0, 0, 0, 0, 0, 1, 2, 4, 5, 6),
    "Rock":          (5, 4, 2, 0, -1, -1, 1, 3, 4, 5),
    "Pop":           (-1, 1, 3, 4, 3, 0, -1, -1, 1, 2),
    "Jazz":          (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Classical":     (4, 3, 2, 1, -1, -1, 0, 2, 3, 4),
    "Hip-Hop":       (5, 4, 1, 3, -1, -1, 1, -1, 2, 3),
    "Electronic":    (5, 4, 1, 0, -2, 1, 0, 1, 4, 5),
    "Acoustic":      (4, 3, 2, 1, 1, 1, 2, 3, 3, 2),
    "Vocal":         (-2, -2, -1, 1, 3, 4, 3, 1, 0, -1),
    "Loudness":      (5, 3, 0, 0, -1, 0, 0, 1, 3, 4),
    "Small Speakers": (-4, -2, 2, 3, 2, 1, 1, 2, 2, 1),
}
CUSTOM = "Custom"


@dataclass
class EqState:
    enabled: bool = False
    preamp: float = 0.0
    gains: list[float] = field(default_factory=lambda: [0.0] * len(BANDS))
    preset: str = "Flat"
    mode: str = "simple"                     # "simple" (10 bands) or "studio" (parametric)
    studio: StudioState = field(default_factory=StudioState)

    @property
    def studio_on(self) -> bool:
        return self.enabled and self.mode == "studio"

    def copy(self) -> "EqState":
        return EqState(self.enabled, self.preamp, list(self.gains), self.preset, self.mode, self.studio.copy())

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "preamp": self.preamp, "gains": self.gains, "preset": self.preset,
                "mode": self.mode, "studio": self.studio.to_dict()}

    @classmethod
    def from_dict(cls, d: dict | None) -> "EqState":
        if not isinstance(d, dict):
            return cls()
        gains = [float(g) for g in d.get("gains", [])][:len(BANDS)]
        gains += [0.0] * (len(BANDS) - len(gains))
        clamp = lambda v, r: max(-r, min(r, float(v)))  # noqa: E731
        return cls(bool(d.get("enabled", False)), clamp(d.get("preamp", 0.0), PREAMP_RANGE),
                   [clamp(g, GAIN_RANGE) for g in gains], str(d.get("preset", CUSTOM)),
                   "studio" if d.get("mode") == "studio" else "simple", StudioState.from_dict(d.get("studio")))


# "Make quiet songs louder": short window so it reacts within a second and adds no start delay.
LOUDNESS_FILTER = "dynaudnorm=f=150:g=7:p=0.9:m=8"
LIMITER = "@eqlim:lavfi=[alimiter=limit=0.97:level=0]"


METER = "@meter:lavfi=[ebur128=metadata=1:peak=true]"


@dataclass
class ChainOptions:
    boost_db: float = 0.0
    loudness: bool = False
    bypass_db: float | None = None        # A/B comparison: original sound, level-matched
    solo: int | None = None               # Studio band audition
    meter: bool = False                   # LUFS + level/correlation meters
    rta: bool = False                     # spectrum analyser
    level_db: float | None = None         # library loudness levelling gain (None = off)
    speaker: object = None                # tools.SpeakerState
    karaoke: tuple = (0.0, 0)             # (vocal reduction 0..1, key change in semitones)


def build_chain(state: EqState, boost_db: float = 0.0, loudness: bool = False,
                bypass_db: float | None = None, meter: bool = False, opts: "ChainOptions | None" = None) -> str:
    """The whole signal chain:
    library level -> loudness levelling -> EQ (simple, or Studio with its tools) -> analyser
    -> speaker routing -> volume boost -> limiter -> meters.
    The boost is a filter (mpv's own volume comes after every filter) so the limiter can
    catch the peaks it creates."""
    from .tools import LEVELS, rta_parts
    o = opts or ChainOptions(boost_db, loudness, bypass_db, None, meter)
    parts = []
    if o.level_db is not None:
        parts.append(f"@lvl:lavfi=[volume=volume={o.level_db:.2f}dB]")
    if o.loudness:
        parts.append(f"@loud:lavfi=[{LOUDNESS_FILTER}]")
    if state.studio_on:
        if o.bypass_db is not None:
            parts.append(f"@ab:lavfi=[volume=volume={o.bypass_db:.2f}dB]")
        else:
            parts += state.studio.chain_parts(o.solo)
    elif state.enabled:
        parts.append(f"@eqpre:lavfi=[volume=volume={state.preamp:.1f}dB]")
        parts += [f"@eq{i}:lavfi=[equalizer=f={f}:t=o:w=1:g={g:.1f}]"
                  for i, (f, g) in enumerate(zip(BANDS, state.gains))]
    vocal, semis = o.karaoke or (0.0, 0)
    if vocal > 0:      # centre (lead vocal) cancellation above 150 Hz; bass kept
        mlev = max(0.015625, 1 - vocal * (1 - 0.015625))
        parts.append(f"@kar:lavfi=[aformat=channel_layouts=stereo,asplit[lo][hi];[lo]lowpass=f=150:p=2[l];"
                     f"[hi]highpass=f=150:p=2,stereotools=mlev={mlev:.4f}[h];[l][h]amix=inputs=2:normalize=0]")
    if semis:          # key change without changing tempo
        r = 2 ** (semis / 12)
        parts.append(f"@key:lavfi=[aresample=48000,asetrate={round(48000 * r)},aresample=48000,atempo={1 / r:.6f}]")
    if o.rta:
        parts += rta_parts()
    speaker = o.speaker.parts() if o.speaker is not None else []
    parts += speaker
    if o.boost_db > 0:
        parts.append(f"@boost:lavfi=[volume=volume={o.boost_db:.2f}dB]")
    if any(not p.startswith("@rta") for p in parts):      # anything that can raise the level
        parts.append(LIMITER)
    if o.meter:
        parts.append(METER)
        if not speaker or "virtual" in (getattr(o.speaker, "mode", "") or ""):
            parts.append(LEVELS)           # the stereo correlation meter needs a stereo signal
    return ",".join(parts)


def af_chain(state: EqState) -> str:
    return build_chain(state)


# ---- 10-band table export / import --------------------------------------------------------
# Nominal (ISO) band centres used in the file; the filters run at BANDS (62 Hz vs 63 Hz is inaudible).
TABLE_FREQS = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
_TABLE_ROW = re.compile(r"^\s*([\d.]+)\s*(?:hz)?\s*[\t,;: ]\s*([-+]?[\d.]+)\s*(?:db)?\s*$", re.I)
_TABLE_PREAMP = re.compile(r"^\s*pre-?amp\s*[:=]?\s*([-+]?[\d.]+)\s*(?:db)?\s*$", re.I)


def export_table(gains: list[float], preamp: float) -> str:
    """Frequency (Hz) / Gain (dB) table, tab separated, with the pre-amp on the last line."""
    lines = ["Frequency (Hz)\tGain (dB)"]
    lines += [f"{f}\t{round(g, 1) + 0.0:.1f}" for f, g in zip(TABLE_FREQS, gains)]   # + 0.0 avoids "-0.0"
    lines.append(f"Preamp: {round(preamp, 1) + 0.0:.1f} dB")
    return "\n".join(lines) + "\n"


def parse_table(text: str) -> tuple[list[float], float]:
    """Read a Frequency/Gain table (tabs, spaces, commas or semicolons). Each row goes to the
    nearest of the 10 bands (within half an octave). Raises ValueError if no rows are found."""
    gains = [0.0] * len(BANDS)
    preamp, found = 0.0, 0
    for line in text.splitlines():
        m = _TABLE_PREAMP.match(line)
        if m:
            preamp = max(-PREAMP_RANGE, min(PREAMP_RANGE, float(m.group(1))))
            continue
        m = _TABLE_ROW.match(line)
        if not m:
            continue                          # header or anything else
        f, g = float(m.group(1)), float(m.group(2))
        if f <= 0:
            continue
        i = min(range(len(BANDS)), key=lambda k: abs(math.log2(f / BANDS[k])))
        if abs(math.log2(f / BANDS[i])) <= 0.5:
            gains[i] = max(-GAIN_RANGE, min(GAIN_RANGE, g))
            found += 1
    if not found:
        raise ValueError("No 'frequency  gain' rows found (for example: 1000<tab>2.0).")
    return gains, preamp


def band_command(index: int, gain: float) -> tuple:
    # Each lavfi wrapper holds one filter, which FFmpeg names Parsed_<filter>_0.
    return ("af-command", f"eq{index}", "gain", f"{gain:.1f}", "Parsed_equalizer_0")


class EqStore:
    """Profiles in QSettings: one shared profile ("*"), or one per output device."""

    def __init__(self, settings) -> None:
        self.s = settings

    def _json(self, key: str, default):
        try:
            value = json.loads(self.s.value(key, "") or "null")
        except (TypeError, ValueError):
            value = None
        return value if isinstance(value, type(default)) else default

    @property
    def per_device(self) -> bool:
        return self.s.value("eq/per_device", False, type=bool)

    @per_device.setter
    def per_device(self, on: bool) -> None:
        self.s.setValue("eq/per_device", on)

    @property
    def normalize(self) -> bool:
        return self.s.value("eq/normalize", False, type=bool)

    @normalize.setter
    def normalize(self, on: bool) -> None:
        self.s.setValue("eq/normalize", on)

    def _key(self, device_id: str | None) -> str:
        return device_id if self.per_device and device_id else "*"

    def load(self, device_id: str | None, fallback: EqState | None = None) -> EqState:
        """Saved profile for this device; a device seen for the first time inherits `fallback`."""
        profiles = self._json("eq/profiles", {})
        saved = profiles.get(self._key(device_id))
        if saved is None and fallback is not None:
            return fallback.copy()
        return EqState.from_dict(saved or profiles.get("*"))

    def save(self, device_id: str | None, state: EqState) -> None:
        profiles = self._json("eq/profiles", {})
        profiles[self._key(device_id)] = state.to_dict()
        self.s.setValue("eq/profiles", json.dumps(profiles))

    def user_presets(self) -> dict[str, list[float]]:
        return self._json("eq/user_presets", {})

    def save_user_preset(self, name: str, gains: list[float]) -> None:
        presets = self.user_presets()
        presets[name] = list(gains)
        self.s.setValue("eq/user_presets", json.dumps(presets))

    def delete_user_preset(self, name: str) -> None:
        presets = self.user_presets()
        presets.pop(name, None)
        self.s.setValue("eq/user_presets", json.dumps(presets))
