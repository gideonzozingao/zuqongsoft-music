"""Studio tools and speaker routing: settings plus the FFmpeg filters that implement them.

Signal flow (see equalizer.build_chain):
  library level -> loudness levelling -> [convolution -> EQ -> tilt -> de-esser -> compressor
  -> bass enhancer -> exciter -> stereo tools -> crossfeed] -> analyser -> speakers
  (upmix / virtual surround / 2.1 crossover / delays) -> volume boost -> limiter -> meters
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

# ---- escaping file paths for FFmpeg filter options (C:\..., spaces) -------------------
def ff_path(path: str) -> str:
    p = path.replace("\\", "/")
    for ch in ("\\", "'", ":", ",", ";", "[", "]", "="):
        p = p.replace(ch, "\\\\" + ch)
    return p


# ---- Studio tools (part of each Studio EQ setup) --------------------------------------
MBAND = {   # 3-band compressor: transfer curve per strength, + make-up gain
    "light": ("-90/-90\\,-24/-24\\,0/-8", 3.0),
    "medium": ("-90/-90\\,-30/-30\\,0/-13", 5.0),
    "strong": ("-90/-90\\,-36/-34\\,0/-18", 7.0),
}


@dataclass
class ToolsState:
    tilt: float = 0.0              # dB, + = brighter, - = warmer
    width: int = 100               # % stereo width (0 = mono, 100 = as recorded)
    balance: int = 0               # -100 (left) .. 100 (right)
    mono: bool = False
    swap: bool = False
    polarity: bool = False
    comp: bool = False
    comp_threshold: float = -18.0  # dB
    comp_ratio: float = 3.0
    comp_attack: float = 20.0      # ms
    comp_release: float = 250.0    # ms
    comp_makeup: float = 3.0       # dB
    mband: bool = False
    mband_amount: str = "medium"
    deess: bool = False
    deess_intensity: float = 0.5   # 0..1
    deess_freq: float = 0.5        # 0..1 (higher = higher frequencies)
    bass: bool = False
    bass_cutoff: float = 150.0     # Hz
    bass_strength: float = 1.5
    exciter: bool = False
    exciter_amount: float = 1.0
    exciter_freq: float = 7500.0
    conv: bool = False
    conv_path: str = ""            # impulse response (WAV) copied into the app's data folder
    conv_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d) -> "ToolsState":
        t = cls()
        if isinstance(d, dict):
            for k, v in d.items():
                if hasattr(t, k):
                    try:
                        setattr(t, k, type(getattr(t, k))(v))
                    except (TypeError, ValueError):
                        pass
        t.mband_amount = t.mband_amount if t.mband_amount in MBAND else "medium"
        return t

    def copy(self) -> "ToolsState":
        return ToolsState.from_dict(self.to_dict())

    def active_names(self) -> list[str]:
        names = []
        if self.conv and self.conv_path:
            names.append("convolution")
        if abs(self.tilt) >= 0.05:
            names.append("tilt")
        if self.deess:
            names.append("de-esser")
        if self.comp:
            names.append("compressor")
        if self.mband:
            names.append("multiband compressor")
        if self.bass:
            names.append("bass enhancer")
        if self.exciter:
            names.append("exciter")
        if self.width != 100 or self.balance or self.mono or self.swap or self.polarity:
            names.append("stereo tools")
        return names

    # chain parts that come before the EQ bands / after them
    def pre_parts(self) -> list[str]:
        if self.conv and self.conv_path:
            return [f"@conv:lavfi=[amovie=filename={ff_path(self.conv_path)}[ir];anull[x];[x][ir]afir]"]
        return []

    def post_parts(self) -> list[str]:
        p = []
        if abs(self.tilt) >= 0.05:
            p.append(f"@tilt:lavfi=[tiltshelf=f=1000:t=q:w=0.5:g={self.tilt:.2f}]")
        if self.deess:
            p.append(f"@deess:lavfi=[deesser=i={self.deess_intensity:.2f}:m=0.5:f={self.deess_freq:.2f}]")
        if self.comp:
            thr = 10 ** (max(-60.0, min(0.0, self.comp_threshold)) / 20)
            makeup = max(1.0, min(64.0, 10 ** (self.comp_makeup / 20)))
            p.append(f"@comp:lavfi=[acompressor=threshold={max(thr, 0.000977):.6f}:ratio={self.comp_ratio:.2f}:"
                     f"attack={self.comp_attack:.1f}:release={self.comp_release:.1f}:makeup={makeup:.3f}:knee=2.83]")
        if self.mband:
            curve, makeup = MBAND[self.mband_amount]
            bands = (f"0.01\\,0.2 6 {curve} 200 | 0.005\\,0.12 6 {curve} 3000 | "
                     f"0.002\\,0.08 6 {curve} 20000")
            p.append(f"@mband:lavfi=[mcompand={bands},volume=volume={makeup:.1f}dB]")
        if self.bass:
            p.append(f"@bass:lavfi=[virtualbass=cutoff={self.bass_cutoff:.0f}:strength={self.bass_strength:.2f}]")
        if self.exciter:
            p.append(f"@exc:lavfi=[aexciter=amount={self.exciter_amount:.2f}:freq={self.exciter_freq:.0f}]")
        stereo = []
        opts = []
        if self.balance:
            opts.append(f"balance_out={self.balance / 100:.2f}")
        if self.swap:
            opts.append("mode=lr>rl")
        if self.polarity:
            opts.append("phasel=1:phaser=1")
        if opts:
            stereo.append("stereotools=" + ":".join(opts))
        if self.mono:
            stereo.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")
        elif self.width != 100:
            stereo.append(f"extrastereo=m={self.width / 100:.2f}:c=0")
        if stereo:
            p.append("@stereo:lavfi=[aformat=channel_layouts=stereo," + ",".join(stereo) + "]")
        return p


# ---- speakers (global, in the Sound tab) -------------------------------------------------
SPEAKER_MODES = (("off", "Off"), ("upmix", "Stereo → 5.1 surround"),
                 ("virtual", "Virtual surround for headphones"), ("sub", "2.1: subwoofer crossover"))
KEMAR_URL = "https://raw.githubusercontent.com/hoene/libmysofa/main/share/MIT_KEMAR_normal_pinna.sofa"


@dataclass
class SpeakerState:
    mode: str = "off"
    crossover: float = 80.0        # Hz, for 2.1
    delay_l: float = 0.0           # ms
    delay_r: float = 0.0
    delay_sub: float = 0.0
    sofa_path: str = ""

    def parts(self) -> list[str]:
        d = (self.delay_l, self.delay_r)
        if self.mode == "upmix":
            return ["@spk:lavfi=[aformat=channel_layouts=stereo,surround=chl_out=5.1]"]
        if self.mode == "virtual" and self.sofa_path:
            return [f"@spk:lavfi=[aformat=channel_layouts=stereo,surround=chl_out=5.1,"
                    f"sofalizer=sofa={ff_path(self.sofa_path)}:type=freq:radius=1]"]
        if self.mode == "sub":
            f = max(40.0, min(200.0, self.crossover))
            graph = (f"aformat=channel_layouts=stereo,asplit[a][b];"
                     f"[a]highpass=f={f:.0f}:p=2,highpass=f={f:.0f}:p=2[m];"          # Linkwitz-Riley 4th order
                     f"[b]lowpass=f={f:.0f}:p=2,lowpass=f={f:.0f}:p=2,pan=mono|c0=0.5*c0+0.5*c1[s];"
                     f"[m][s]amerge=inputs=2,pan=2.1|FL=c0|FR=c1|LFE=c2")
            if any(d) or self.delay_sub:
                graph += f",adelay=delays={d[0]:.2f}|{d[1]:.2f}|{self.delay_sub:.2f}:all=0"
            return [f"@spk:lavfi=[{graph}]"]
        if any(d):
            return [f"@spk:lavfi=[adelay=delays={d[0]:.2f}|{d[1]:.2f}:all=0]"]
        return []

    def describe(self) -> str:
        return {"upmix": "5.1 upmix", "virtual": "virtual surround", "sub": f"2.1 crossover {self.crossover:.0f} Hz"
                }.get(self.mode, "") + (" + speaker delay" if (self.delay_l or self.delay_r) else "")


# ---- analyser (third-octave RTA) and meters --------------------------------------------
RTA_FREQS = [round(25 * 2 ** (k / 3)) for k in range(30)]          # 25 Hz .. ~12.7 kHz


def rta_parts() -> list[str]:
    """Measures a filtered mono copy in an extra channel, then drops it: the music itself is
    untouched (verified bit-identical)."""
    parts = ["@rtafmt:lavfi=[aformat=channel_layouts=stereo]"]
    for i, f in enumerate(RTA_FREQS):
        parts.append(f"@rta{i}:lavfi=[pan=3.0|c0=c0|c1=c1|c2=0.5*c0+0.5*c1,bandpass=f={f}:t=q:w=4.32:channels=FC,"
                     f"astats=metadata=1:reset=1:measure_overall=none:measure_perchannel=RMS_level,"
                     f"pan=stereo|c0=c0|c1=c1]")
    return parts


LEVELS = ("@levels:lavfi=[astats=metadata=1:reset=1:measure_overall=none:"
          "measure_perchannel=Peak_level+RMS_level,aphasemeter=video=0]")


# ---- target curves and measurements ----------------------------------------------------------
# Approximations of well-known targets (dB, normalised to 0 at 1 kHz). For exact curves,
# import the CSV from AutoEq's "targets" folder.
TARGETS = {
    "flat": ("Flat", [(20, 0), (20000, 0)]),
    "harman_oe": ("Harman over-ear 2018 (approx.)",
                  [(20, 6.5), (60, 6.3), (100, 5.5), (200, 2.0), (400, 0.3), (1000, 0), (1500, 1.5), (2000, 4.0),
                   (2800, 8.5), (3500, 8.0), (4500, 5.5), (6000, 2.5), (8000, 1.0), (10000, -1.0),
                   (13000, -3.5), (16000, -5.5), (20000, -9.0)]),
    "harman_ie": ("Harman in-ear 2019 (approx.)",
                  [(20, 9.5), (60, 9.3), (100, 8.5), (200, 5.0), (400, 1.5), (1000, 0), (1500, 1.5), (2000, 4.5),
                   (2800, 9.0), (3500, 9.5), (4500, 7.5), (6000, 4.0), (8000, 1.0), (10000, -1.5),
                   (13000, -4.0), (16000, -7.0), (20000, -10.0)]),
    "warm": ("Warm preference", [(20, 4), (150, 3), (500, 0.5), (1000, 0), (4000, -1), (10000, -3), (20000, -4)]),
    "bright": ("Bright preference", [(20, 0), (500, 0), (1000, 0), (3000, 1.5), (8000, 3), (20000, 3)]),
}


def interp_db(points: list[tuple[float, float]], f: float) -> float:
    """Log-frequency linear interpolation; flat beyond the ends."""
    if not points:
        return 0.0
    if f <= points[0][0]:
        return points[0][1]
    if f >= points[-1][0]:
        return points[-1][1]
    lf = math.log10(f)
    for (f0, d0), (f1, d1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            t = (lf - math.log10(f0)) / max(1e-9, math.log10(f1) - math.log10(f0))
            return d0 + (d1 - d0) * t
    return points[-1][1]


def parse_curve_csv(text: str, points: int = 160) -> list[tuple[float, float]]:
    """Frequency-response CSV (AutoEq 'frequency,raw', REW exports, ...): first two numbers per line.
    Normalised to 0 dB at 1 kHz and resampled to `points` log-spaced points."""
    raw = []
    for line in text.splitlines():
        nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)
        if len(nums) >= 2:
            try:
                f, db = float(nums[0]), float(nums[1])
            except ValueError:
                continue
            if 5 <= f <= 40000:
                raw.append((f, db))
    raw.sort()
    if len(raw) < 5:
        raise ValueError("Expected lines of 'frequency, dB' (for example: 1000, 85.2).")
    ref = interp_db(raw, 1000)
    lo, hi = math.log10(20), math.log10(20000)
    grid = [10 ** (lo + (hi - lo) * i / (points - 1)) for i in range(points)]
    return [(round(f, 2), round(interp_db(raw, f) - ref, 2)) for f in grid]
