"""Equalizer model: 10 octave bands, presets, the mpv filter chain and saved profiles.

Each band is an FFmpeg peaking filter (`equalizer`, one octave wide). A pre-amp
(`volume`) comes first and a limiter (`alimiter`) last, so boosts don't clip.
Every filter is labelled, so a slider move updates one gain live, without
rebuilding the chain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

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

    def copy(self) -> "EqState":
        return EqState(self.enabled, self.preamp, list(self.gains), self.preset)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "preamp": self.preamp, "gains": self.gains, "preset": self.preset}

    @classmethod
    def from_dict(cls, d: dict | None) -> "EqState":
        if not isinstance(d, dict):
            return cls()
        gains = [float(g) for g in d.get("gains", [])][:len(BANDS)]
        gains += [0.0] * (len(BANDS) - len(gains))
        clamp = lambda v, r: max(-r, min(r, float(v)))  # noqa: E731
        return cls(bool(d.get("enabled", False)), clamp(d.get("preamp", 0.0), PREAMP_RANGE),
                   [clamp(g, GAIN_RANGE) for g in gains], str(d.get("preset", CUSTOM)))


# "Make quiet songs louder": short window so it reacts within a second and adds no start delay.
LOUDNESS_FILTER = "dynaudnorm=f=150:g=7:p=0.9:m=8"
LIMITER = "@eqlim:lavfi=[alimiter=limit=0.97:level=0]"


def build_chain(state: EqState, boost_db: float = 0.0, loudness: bool = False) -> str:
    """Filter chain: loudness levelling -> EQ (pre-amp + bands) -> volume boost -> limiter.
    The boost is a filter (not mpv's volume, which is applied after all filters) so the
    limiter can catch the peaks it creates: louder without clipping."""
    parts = []
    if loudness:
        parts.append(f"@loud:lavfi=[{LOUDNESS_FILTER}]")
    if state.enabled:
        parts.append(f"@eqpre:lavfi=[volume=volume={state.preamp:.1f}dB]")
        parts += [f"@eq{i}:lavfi=[equalizer=f={f}:t=o:w=1:g={g:.1f}]"
                  for i, (f, g) in enumerate(zip(BANDS, state.gains))]
    if boost_db > 0:
        parts.append(f"@boost:lavfi=[volume=volume={boost_db:.2f}dB]")
    if parts:
        parts.append(LIMITER)
    return ",".join(parts)


def af_chain(state: EqState) -> str:
    return build_chain(state)


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
