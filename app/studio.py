"""Studio EQ: a parametric equalizer with real frequency-response maths.

Each band is one FFmpeg biquad filter. The curve the UI draws is computed from the
same RBJ "Audio EQ Cookbook" formulas FFmpeg uses, so what you see is what you hear.
Also: AutoEq / Equalizer APO "ParametricEQ.txt" import and export.
"""
from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field

from .tools import ToolsState

FS = 48000.0
F_MIN, F_MAX = 20.0, 20000.0
GAIN_LIMIT = 24.0
Q_MIN, Q_MAX = 0.1, 30.0
MAX_BANDS = 16

# type -> (label, FFmpeg filter, has gain)
TYPES = {
    "peak": ("Peak", "equalizer", True),
    "lowshelf": ("Low shelf", "lowshelf", True),
    "highshelf": ("High shelf", "highshelf", True),
    "lowcut": ("Low cut", "highpass", False),
    "highcut": ("High cut", "lowpass", False),
    "notch": ("Notch", "bandreject", False),
}
DEFAULT_Q = {"peak": 1.0, "lowshelf": 0.71, "highshelf": 0.71, "lowcut": 0.71, "highcut": 0.71, "notch": 8.0}


@dataclass
class Band:
    type: str = "peak"
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 1.0
    enabled: bool = True

    @property
    def has_gain(self) -> bool:
        return TYPES[self.type][2]

    def filter_expr(self) -> str:
        name = TYPES[self.type][1]
        if self.type in ("lowcut", "highcut"):
            return f"{name}=f={self.freq:.1f}:p=2:t=q:w={self.q:.3f}"
        if self.type == "notch":
            return f"{name}=f={self.freq:.1f}:t=q:w={self.q:.3f}"
        return f"{name}=f={self.freq:.1f}:t=q:w={self.q:.3f}:g={self.gain:.2f}"

    def to_dict(self) -> dict:
        return {"type": self.type, "freq": self.freq, "gain": self.gain, "q": self.q, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict) -> "Band":
        t = d.get("type", "peak") if d.get("type") in TYPES else "peak"
        clamp = lambda v, lo, hi: max(lo, min(hi, float(v)))  # noqa: E731
        return cls(t, clamp(d.get("freq", 1000), F_MIN, F_MAX), clamp(d.get("gain", 0), -GAIN_LIMIT, GAIN_LIMIT),
                   clamp(d.get("q", DEFAULT_Q[t]), Q_MIN, Q_MAX), bool(d.get("enabled", True)))

    # -- RBJ cookbook biquad, as FFmpeg implements it --
    def coefficients(self, fs: float = FS) -> tuple[tuple, tuple]:
        w0 = 2 * math.pi * min(self.freq, fs * 0.49) / fs
        cos, sin = math.cos(w0), math.sin(w0)
        alpha = sin / (2 * self.q)
        A = 10 ** (self.gain / 40)
        t = self.type
        if t == "peak":
            b, a = (1 + alpha * A, -2 * cos, 1 - alpha * A), (1 + alpha / A, -2 * cos, 1 - alpha / A)
        elif t in ("lowshelf", "highshelf"):
            s = 2 * math.sqrt(A) * alpha
            if t == "lowshelf":
                b = (A * ((A + 1) - (A - 1) * cos + s), 2 * A * ((A - 1) - (A + 1) * cos),
                     A * ((A + 1) - (A - 1) * cos - s))
                a = ((A + 1) + (A - 1) * cos + s, -2 * ((A - 1) + (A + 1) * cos), (A + 1) + (A - 1) * cos - s)
            else:
                b = (A * ((A + 1) + (A - 1) * cos + s), -2 * A * ((A - 1) + (A + 1) * cos),
                     A * ((A + 1) + (A - 1) * cos - s))
                a = ((A + 1) - (A - 1) * cos + s, 2 * ((A - 1) - (A + 1) * cos), (A + 1) - (A - 1) * cos - s)
        elif t == "lowcut":
            b, a = ((1 + cos) / 2, -(1 + cos), (1 + cos) / 2), (1 + alpha, -2 * cos, 1 - alpha)
        elif t == "highcut":
            b, a = ((1 - cos) / 2, 1 - cos, (1 - cos) / 2), (1 + alpha, -2 * cos, 1 - alpha)
        else:   # notch
            b, a = (1, -2 * cos, 1), (1 + alpha, -2 * cos, 1 - alpha)
        return b, a

    def response_db(self, freqs: list[float], fs: float = FS) -> list[float]:
        b, a = self.coefficients(fs)
        out = []
        for f in freqs:
            z = cmath.exp(-1j * 2 * math.pi * f / fs)
            h = (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
            out.append(20 * math.log10(max(abs(h), 1e-9)))
        return out


def default_bands() -> list[Band]:
    return [Band("lowshelf", 105, 0, 0.71), Band("peak", 250, 0, 1.0), Band("peak", 1000, 0, 1.0),
            Band("peak", 4000, 0, 1.0), Band("highshelf", 10000, 0, 0.71)]


@dataclass
class StudioState:
    bands: list[Band] = field(default_factory=default_bands)
    preamp: float = 0.0
    crossfeed: bool = False
    crossfeed_strength: float = 0.3
    name: str = "Studio"
    tools: ToolsState = field(default_factory=ToolsState)
    target: str = "none"                                   # key in tools.TARGETS, "custom" or "none"
    target_custom: list = field(default_factory=list)      # [(Hz, dB)] imported target
    measurement: list = field(default_factory=list)        # [(Hz, dB)] imported headphone response
    measurement_name: str = ""

    def copy(self) -> "StudioState":
        return StudioState.from_dict(self.to_dict())

    def to_dict(self) -> dict:
        return {"bands": [b.to_dict() for b in self.bands], "preamp": self.preamp, "crossfeed": self.crossfeed,
                "crossfeed_strength": self.crossfeed_strength, "name": self.name, "tools": self.tools.to_dict(),
                "target": self.target, "target_custom": [list(p) for p in self.target_custom],
                "measurement": [list(p) for p in self.measurement], "measurement_name": self.measurement_name}

    @classmethod
    def from_dict(cls, d) -> "StudioState":
        if not isinstance(d, dict):
            return cls()
        bands = [Band.from_dict(b) for b in d.get("bands", []) if isinstance(b, dict)][:MAX_BANDS]
        pts = lambda key: [(float(a), float(b)) for a, b in d.get(key, []) if isinstance(a, (int, float))]  # noqa: E731
        return cls(bands, max(-GAIN_LIMIT, min(12.0, float(d.get("preamp", 0.0)))), bool(d.get("crossfeed", False)),
                   max(0.05, min(0.9, float(d.get("crossfeed_strength", 0.3)))), str(d.get("name", "Studio")),
                   ToolsState.from_dict(d.get("tools")), str(d.get("target", "none")), pts("target_custom"),
                   pts("measurement"), str(d.get("measurement_name", "")))

    # -- curve --
    @staticmethod
    def grid(n: int = 240) -> list[float]:
        lo, hi = math.log10(F_MIN), math.log10(F_MAX)
        return [10 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]

    def response(self, freqs: list[float], include_preamp: bool = True) -> list[float]:
        total = [self.preamp if include_preamp else 0.0] * len(freqs)
        for b in self.bands:
            if b.enabled:
                total = [t + r for t, r in zip(total, b.response_db(freqs))]
        return total

    def auto_preamp(self) -> float:
        """Pre-amp that keeps the loudest point of the curve at 0 dB."""
        peak = max(self.response(self.grid(400), include_preamp=False))
        return round(-max(0.0, peak), 1)

    def loudness_offset(self) -> float:
        """Approximate loudness change of the whole EQ (pink-noise weighting, 50 Hz-12 kHz),
        used to level-match the A/B comparison."""
        lo, hi, n = math.log10(50), math.log10(12000), 120
        freqs = [10 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]
        power = [10 ** (r / 10) for r in self.response(freqs)]
        return 10 * math.log10(sum(power) / len(power))

    def chain_parts(self, solo: int | None = None) -> list[str]:
        """solo = index of a band to audition on its own (a band-pass around it)."""
        if solo is not None and 0 <= solo < len(self.bands):
            b = self.bands[solo]
            q = b.q if b.type in ("peak", "notch") else 2.0
            return [f"@solo:lavfi=[bandpass=f={b.freq:.1f}:t=q:w={max(0.5, min(8.0, q)):.2f},volume=volume=6dB]"]
        parts = self.tools.pre_parts()
        parts.append(f"@sbpre:lavfi=[volume=volume={self.preamp:.1f}dB]")
        parts += [f"@sb{i}:lavfi=[{b.filter_expr()}]" for i, b in enumerate(self.bands) if b.enabled]
        parts += self.tools.post_parts()
        if self.crossfeed:
            parts.append(f"@xf:lavfi=[crossfeed=strength={self.crossfeed_strength:.2f}:range=0.5]")
        return parts


def live_commands(old: StudioState, new: StudioState) -> list[tuple] | None:
    """af-commands that turn `old` into `new` without rebuilding the chain,
    or None when the structure changed (bands added/removed/retyped/toggled, crossfeed on/off)."""
    if (len(old.bands) != len(new.bands) or old.crossfeed != new.crossfeed or old.tools != new.tools
            or any(a.type != b.type or a.enabled != b.enabled for a, b in zip(old.bands, new.bands))):
        return None
    cmds = []
    if old.preamp != new.preamp:
        cmds.append(("af-command", "sbpre", "volume", f"{new.preamp:.1f}dB", "Parsed_volume_0"))
    for i, (a, b) in enumerate(zip(old.bands, new.bands)):
        if not b.enabled:
            continue
        target = f"Parsed_{TYPES[b.type][1]}_0"
        if a.freq != b.freq:
            cmds.append(("af-command", f"sb{i}", "f", f"{b.freq:.1f}", target))
        if a.q != b.q:
            cmds.append(("af-command", f"sb{i}", "w", f"{b.q:.3f}", target))
        if b.has_gain and a.gain != b.gain:
            cmds.append(("af-command", f"sb{i}", "g", f"{b.gain:.2f}", target))
    if new.crossfeed and old.crossfeed_strength != new.crossfeed_strength:
        cmds.append(("af-command", "xf", "strength", f"{new.crossfeed_strength:.2f}", "Parsed_crossfeed_0"))
    return cmds


# ---- AutoEq / Equalizer APO ParametricEQ.txt ------------------------------------------
_APO_TYPES = {"PK": "peak", "PEQ": "peak", "MODAL": "peak", "LS": "lowshelf", "LSC": "lowshelf",
              "LSQ": "lowshelf", "HS": "highshelf", "HSC": "highshelf", "HSQ": "highshelf",
              "HP": "lowcut", "HPQ": "lowcut", "LP": "highcut", "LPQ": "highcut", "NO": "notch"}
_EXPORT_TYPES = {"peak": "PK", "lowshelf": "LSC", "highshelf": "HSC", "lowcut": "HPQ", "highcut": "LPQ",
                 "notch": "NO"}
_FILTER = re.compile(r"^\s*Filter\s*\d*\s*:\s*(ON|OFF)\s+([A-Za-z]+)\s+Fc\s+([\d.]+)\s*Hz"
                     r"(?:\s+Gain\s+([-+\d.]+)\s*dB)?(?:\s+Q\s+([\d.]+))?", re.I)
_PREAMP = re.compile(r"^\s*Preamp\s*:\s*([-+\d.]+)\s*dB", re.I)


def parse_parametric(text: str) -> StudioState:
    """Parse AutoEq / Equalizer APO parametric EQ text. Raises ValueError if nothing usable."""
    st = StudioState(bands=[], name="Imported")
    skipped = 0
    for line in text.splitlines():
        m = _PREAMP.match(line)
        if m:
            st.preamp = max(-GAIN_LIMIT, min(12.0, float(m.group(1))))
            continue
        m = _FILTER.match(line)
        if not m:
            continue
        kind = _APO_TYPES.get(m.group(2).upper())
        if kind is None or len(st.bands) >= MAX_BANDS:
            skipped += 1
            continue
        st.bands.append(Band.from_dict({"type": kind, "freq": m.group(3), "gain": m.group(4) or 0,
                                        "q": m.group(5) or DEFAULT_Q[kind], "enabled": m.group(1).upper() == "ON"}))
    if not st.bands:
        if "GraphicEQ" in text:
            raise ValueError("This is a GraphicEQ file. Download the ParametricEQ.txt version instead.")
        raise ValueError("No 'Filter: ON PK Fc … Hz Gain … dB Q …' lines found.")
    st.skipped = skipped  # type: ignore[attr-defined]
    return st


def export_parametric(st: StudioState) -> str:
    lines = [f"Preamp: {st.preamp:.1f} dB"]
    for i, b in enumerate(st.bands, 1):
        gain = f" Gain {b.gain:.1f} dB" if b.has_gain else ""
        lines.append(f"Filter {i}: {'ON' if b.enabled else 'OFF'} {_EXPORT_TYPES[b.type]} Fc {b.freq:.0f} Hz"
                     f"{gain} Q {b.q:.2f}")
    return "\n".join(lines) + "\n"
