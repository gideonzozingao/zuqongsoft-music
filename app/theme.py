"""Material 3 themes: seed-colour schemes, Qt style sheet, palette and icon factory.

Every theme is generated from one seed colour, the way Material 3 does it: tonal
palettes are built in CIELAB at fixed tones (lightness), so a preset works in
both light and dark mode, and any colour can become a custom theme.
"""
from __future__ import annotations

import math
from functools import lru_cache
from string import Template

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

# name -> seed colour and options. neutral = tint of the surfaces, chroma = force primary
# colourfulness, black = true-black surfaces, force = always light/dark.
PRESETS = {
    "Lavender": dict(seed="#6750A4"),
    "Ocean": dict(seed="#00658F"),
    "Teal": dict(seed="#006A6A"),
    "Forest": dict(seed="#2E7D32"),
    "Sepia": dict(seed="#8A5100", neutral=10),
    "Sunset": dict(seed="#C2410C"),
    "Rose": dict(seed="#B0306A"),
    "Midnight": dict(seed="#0061A4", neutral=12),
    "Graphite": dict(seed="#5F6368", chroma=6),
    "AMOLED": dict(seed="#6750A4", black=True, force="dark"),
}
CUSTOM = "Custom"
ALBUM = "Album art"          # Material You: seed taken from the playing track's cover
DEFAULT_PRESET = "Lavender"
MODES = ("light", "dark", "system")


# ---- colour science ------------------------------------------------------------------
def _lab(hex_: str) -> tuple[float, float, float]:
    c = QColor(hex_)
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
           for x in (c.redF(), c.greenF(), c.blueF())]
    X = (0.4124564 * lin[0] + 0.3575761 * lin[1] + 0.1804375 * lin[2]) / 0.95047
    Y = 0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2]
    Z = (0.0193339 * lin[0] + 0.1191920 * lin[1] + 0.9503041 * lin[2]) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116  # noqa: E731
    fx, fy, fz = f(X), f(Y), f(Z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _linear_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    inv = lambda t: t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)  # noqa: E731
    X, Y, Z = inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883
    return (3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z,
            -0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z,
            0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z)


def _tone(hue: float, chroma: float, lightness: float) -> str:
    """Colour at a given hue/chroma/lightness, reducing chroma until it fits sRGB."""
    c = chroma
    while True:
        lin = _linear_rgb(lightness, c * math.cos(hue), c * math.sin(hue))
        if all(-1e-4 <= v <= 1 + 1e-4 for v in lin) or c <= 0:
            break
        c = max(0.0, c - 0.5)
    gamma = lambda v: 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055  # noqa: E731
    return "#" + "".join(f"{round(max(0.0, min(1.0, gamma(max(0.0, v)))) * 255):02X}" for v in lin)


def make_scheme(seed: str, dark: bool, neutral: float = 4, chroma: float | None = None,
                black: bool = False) -> dict[str, str]:
    L, a, b = _lab(seed)
    hue = math.atan2(b, a)
    pc = chroma if chroma is not None else max(math.hypot(a, b), 40)
    sc, nv = min(pc / 3, 14), max(neutral * 2, 8)
    P = lambda t: _tone(hue, pc, t)       # noqa: E731
    S = lambda t: _tone(hue, sc, t)       # noqa: E731
    N = lambda t: _tone(hue, neutral, t)  # noqa: E731
    V = lambda t: _tone(hue, nv, t)       # noqa: E731
    if dark:
        s = dict(primary=P(80), on_primary=P(20), primary_container=P(30), on_primary_container=P(90),
                 secondary_container=S(30), on_secondary_container=S(90),
                 surface=N(6), surface_container_lowest=N(4), surface_container_low=N(10),
                 surface_container=N(12), surface_container_high=N(17), surface_container_highest=N(22),
                 on_surface=N(90), on_surface_variant=V(80), outline=V(60), outline_variant=V(30),
                 inverse_surface=N(90), inverse_on_surface=N(20), error="#F2B8B5")
        if black:
            s.update(surface="#000000", surface_container_lowest="#000000", surface_container_low=N(4),
                     surface_container=N(6), surface_container_high=N(10), surface_container_highest=N(15))
        return s
    return dict(primary=P(40), on_primary="#FFFFFF", primary_container=P(90), on_primary_container=P(10),
                secondary_container=S(90), on_secondary_container=S(10),
                surface=N(98), surface_container_lowest="#FFFFFF", surface_container_low=N(96),
                surface_container=N(94), surface_container_high=N(92), surface_container_highest=N(90),
                on_surface=N(10), on_surface_variant=V(30), outline=V(50), outline_variant=V(80),
                inverse_surface=N(20), inverse_on_surface=N(95), error="#B3261E")


# Colour pulled from the current cover (None until a cover has been seen).
art_seed: str | None = None
art_vivid = True


def seed_from_image(img) -> tuple[str, bool] | None:
    """Dominant *colourful* hue of an image, as (hex, vivid). Greys and near-blacks are
    ignored, like Material You; a mostly grey cover returns a muted seed instead."""
    if img is None or img.isNull():
        return None
    from PySide6.QtGui import QImage
    small = img.convertToFormat(QImage.Format.Format_RGB32).scaled(
        48, 48, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    buckets = [[0.0, 0.0, 0.0, 0.0] for _ in range(36)]      # weight, r, g, b
    grey = [0.0, 0.0, 0.0, 0.0]
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            h, sat, val = c.hsvHueF(), c.hsvSaturationF(), c.valueF()
            if val < 0.12:
                continue
            if h < 0 or sat < 0.18:
                grey[0] += 1
                grey[1] += c.red(); grey[2] += c.green(); grey[3] += c.blue()   # noqa: E702
                continue
            w = (sat * val) ** 2                         # favour vivid colours
            b = buckets[int(h * 36) % 36]
            b[0] += w
            b[1] += c.red() * w; b[2] += c.green() * w; b[3] += c.blue() * w   # noqa: E702
    total = sum(b[0] for b in buckets)
    if total < 2.0:                                     # essentially a grey/black/white cover
        if not grey[0]:
            return None
        return QColor(int(grey[1] / grey[0]), int(grey[2] / grey[0]), int(grey[3] / grey[0])).name(), False
    # Score each hue with its neighbours so a colour split across two buckets still wins.
    best = max(range(36), key=lambda i: buckets[i][0] + 0.5 * (buckets[i - 1][0] + buckets[(i + 1) % 36][0]))
    group = [buckets[(best + d) % 36] for d in (-1, 0, 1)]
    w = sum(g[0] for g in group)
    rgb = [sum(g[k] for g in group) / w for k in (1, 2, 3)]
    return QColor(*(int(max(0, min(255, v))) for v in rgb)).name(), True


def scheme_for(preset: str, dark: bool, custom_seed: str | None = None) -> dict[str, str]:
    if preset == ALBUM:
        if not art_seed:
            return make_scheme(PRESETS[DEFAULT_PRESET]["seed"], dark, neutral=6)
        return make_scheme(art_seed, dark, neutral=6 if art_vivid else 1, chroma=None if art_vivid else 4)
    if preset == CUSTOM:
        return make_scheme(custom_seed or PRESETS[DEFAULT_PRESET]["seed"], dark)
    opts = dict(PRESETS.get(preset, PRESETS[DEFAULT_PRESET]))
    seed = opts.pop("seed")
    force = opts.pop("force", None)
    return make_scheme(seed, force == "dark" if force else dark, **opts)


# The live colour roles. Updated in place by apply(), so `from .theme import C` stays valid.
C: dict[str, str] = make_scheme(PRESETS[DEFAULT_PRESET]["seed"], dark=True)
is_dark = True


def _rgba(hex_: str, alpha: float) -> str:
    c = QColor(hex_)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _blend(base: str, layer: str, alpha: float) -> str:
    """M3 state layer: `layer` colour over `base` at `alpha` opacity."""
    b, l = QColor(base), QColor(layer)
    mix = lambda x, y: round(x + (y - x) * alpha)  # noqa: E731
    return QColor(mix(b.red(), l.red()), mix(b.green(), l.green()), mix(b.blue(), l.blue())).name()


def _tokens() -> dict[str, str]:
    t = dict(C)
    t.update(
        hover=_rgba(C["on_surface"], 0.08),
        pressed=_rgba(C["on_surface"], 0.12),
        disabled=_rgba(C["on_surface"], 0.38),
        disabled_container=_rgba(C["on_surface"], 0.12),
        primary_hover=_rgba(C["primary"], 0.08),
        primary_pressed=_rgba(C["primary"], 0.12),
        primary_hi=_blend(C["primary"], C["on_primary"], 0.08),
        primary_lo=_blend(C["primary"], C["on_primary"], 0.12),
        tonal_hi=_blend(C["secondary_container"], C["on_secondary_container"], 0.08),
    )
    return t


QSS = Template(r"""
* { font-size: 14px; }
QWidget { color: $on_surface; }
QMainWindow, QWidget#root { background: $surface; }
QDialog { background: $surface_container_high; }
QWidget#pane { background: $surface_container_low; border-radius: 24px; }
QWidget#bar, QWidget#drawer { background: $surface; }
QLabel { background: transparent; }
QLabel#brand { font-size: 22px; padding: 12px 0 18px 28px; }
QLabel#headline { font-size: 28px; }
QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#artist { font-size: 15px; color: $on_surface_variant; }
QLabel#muted, QLabel#time { color: $on_surface_variant; }
QLabel#time { font-size: 12px; }
QLabel#empty { color: $on_surface_variant; font-size: 16px; }

QMenuBar { background: $surface; border: none; padding: 4px 8px; }
QMenuBar::item { background: transparent; padding: 6px 12px; border-radius: 16px; }
QMenuBar::item:selected { background: $hover; }
QMenuBar::item:pressed { background: $pressed; }
QMenu { background: $surface_container; border: 1px solid $outline_variant; border-radius: 4px; padding: 8px 0; }
QMenu::item { background: transparent; padding: 10px 32px 10px 16px; }
QMenu::item:selected { background: $hover; }
QMenu::item:disabled { color: $disabled; }
QMenu::icon { padding-left: 12px; }
QMenu::separator { height: 1px; background: $outline_variant; margin: 8px 0; }
QToolTip { background: $inverse_surface; color: $inverse_on_surface; border: none; border-radius: 4px; padding: 4px 8px; }
QStatusBar { background: $surface; color: $on_surface_variant; }
QStatusBar::item { border: none; }

QListWidget#nav { background: transparent; border: none; outline: 0; }
QListWidget#nav::item { color: $on_surface_variant; border-radius: 28px; padding-left: 16px; margin: 0 12px; }
QListWidget#nav::item:hover { background: $hover; }
QListWidget#nav::item:selected { background: $secondary_container; color: $on_secondary_container; }
QListWidget#nav::item:disabled { background: transparent; color: $on_surface_variant; }

QLineEdit { background: transparent; border: 1px solid $outline; border-radius: 4px; padding: 10px 12px;
            selection-background-color: $primary; selection-color: $on_primary; }
QLineEdit:focus { border: 2px solid $primary; padding: 9px 11px; }
QLineEdit#search, QLineEdit#search:focus { background: $surface_container_high; border: none;
            border-radius: 24px; padding: 0 16px 0 8px; }
QLineEdit#search:focus { background: $surface_container_highest; }
QPlainTextEdit { background: $surface_container; border: 1px solid $outline_variant; border-radius: 12px;
             padding: 8px; selection-background-color: $primary; selection-color: $on_primary; }
QPlainTextEdit:focus { border: 2px solid $primary; padding: 7px; }
QComboBox { background: transparent; border: 1px solid $outline; border-radius: 4px; padding: 8px 12px; }
QComboBox QAbstractItemView { background: $surface_container; border: none; outline: 0;
            selection-background-color: $secondary_container; selection-color: $on_secondary_container; }

QTableView { background: transparent; border: none; outline: 0; gridline-color: transparent;
             selection-background-color: $secondary_container; selection-color: $on_secondary_container; }
QTableView::item { padding: 0 12px; border: none; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $on_surface_variant; border: none;
             border-bottom: 1px solid $outline_variant; padding: 10px 12px; font-size: 13px; font-weight: 600; }
QHeaderView::section:hover { color: $on_surface; }
QTableCornerButton::section { background: transparent; border: none; }
QListView#grid { background: transparent; border: none; outline: 0; }

QPushButton { background: transparent; color: $primary; border: none; border-radius: 20px;
              padding: 0 16px; min-height: 40px; font-weight: 600; }
QPushButton:hover { background: $primary_hover; }
QPushButton:pressed { background: $primary_pressed; }
QPushButton:disabled { color: $disabled; }
QPushButton[kind="filled"] { background: $primary; color: $on_primary; padding: 0 24px 0 16px; }
QPushButton[kind="filled"]:hover { background: $primary_hi; }
QPushButton[kind="filled"]:pressed { background: $primary_lo; }
QPushButton[kind="tonal"] { background: $secondary_container; color: $on_secondary_container; padding: 0 24px 0 16px; }
QPushButton[kind="tonal"]:hover { background: $tonal_hi; }
QPushButton[kind="filled"]:disabled, QPushButton[kind="tonal"]:disabled { background: $disabled_container; color: $disabled; }
QDialogButtonBox QPushButton { min-width: 64px; }

QToolButton { background: transparent; border: none; border-radius: 20px; }
QToolButton:hover { background: $hover; }
QToolButton:pressed { background: $pressed; }
QToolButton[active="true"] { background: $secondary_container; }
QToolButton[active="true"]:hover { background: $tonal_hi; }
QToolButton#chip { border: 1px solid $outline_variant; border-radius: 8px; padding: 0 12px 0 8px;
             min-height: 32px; max-height: 32px; color: $on_surface_variant; font-size: 13px; font-weight: 600; }
QToolButton#chip:hover { background: $hover; }
QToolButton#chip[bt="true"] { border-color: $primary; color: $primary; }
QToolButton#chip::menu-indicator { image: none; width: 0; }
QLabel#dialogTitle { font-size: 24px; }
QPushButton#segment { background: transparent; color: $on_surface; border: 1px solid $outline;
             padding: 0 20px; min-height: 40px; font-weight: 600; }
QPushButton#segment[seg="first"] { border-radius: 0; border-top-left-radius: 20px; border-bottom-left-radius: 20px; }
QPushButton#segment[seg="mid"] { border-radius: 0; border-left: none; }
QPushButton#segment[seg="last"] { border-radius: 0; border-left: none;
             border-top-right-radius: 20px; border-bottom-right-radius: 20px; }
QPushButton#segment:hover { background: $hover; }
QPushButton#segment:checked { background: $secondary_container; color: $on_secondary_container; }
QPushButton#segment:disabled { color: $disabled; border-color: $disabled_container; }
QToolButton#themeCard { border: 1px solid $outline_variant; border-radius: 16px; padding: 8px;
             color: $on_surface; font-weight: 600; }
QToolButton#themeCard:hover { background: $hover; }
QToolButton#themeCard:checked { border: 2px solid $primary; background: $secondary_container;
             color: $on_secondary_container; padding: 7px; }
QPushButton#segment[small="true"] { padding: 0 6px; min-height: 36px; max-height: 36px; font-size: 13px; }
QPushButton#segment[small="true"][seg="first"] { border-top-left-radius: 18px; border-bottom-left-radius: 18px; }
QPushButton#segment[small="true"][seg="last"] { border-top-right-radius: 18px; border-bottom-right-radius: 18px; }
QToolButton#iconMenu::menu-indicator { image: none; width: 0; }
QLabel#miniTitle { font-size: 14px; font-weight: 600; }
QFrame#card { background: $surface_container; border-radius: 20px; }
QLabel#cardTitle { font-size: 16px; font-weight: 600; }
QLabel#kpi { font-size: 30px; font-weight: 600; color: $primary; }
QLabel#hero { font-size: 26px; font-weight: 600; color: $primary; }
QLabel#rank { font-size: 15px; font-weight: 600; color: $primary; }
QPushButton#listRow { background: transparent; color: $on_surface; border-radius: 12px; padding: 0;
             min-height: 40px; font-weight: 400; text-align: left; }
QPushButton#listRow:hover { background: $hover; }
QPushButton#tile { background: transparent; border: none; }
QListView#queue { background: transparent; border: none; outline: 0; }
QToolButton#fab { background: $primary; border-radius: 20px; }
QToolButton#fab:hover { background: $primary_hi; }
QToolButton#fab:pressed { background: $primary_lo; }

QSlider { background: transparent; }
QSlider:horizontal { min-height: 24px; }
QSlider:vertical { min-width: 28px; min-height: 180px; }
QSlider::groove:horizontal { height: 4px; background: $secondary_container; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $primary; border-radius: 2px; }
QSlider::add-page:horizontal { background: $secondary_container; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: $primary; }
QSlider::handle:horizontal:hover { background: $primary_hi; }

QSlider::groove:vertical { width: 4px; background: $secondary_container; border-radius: 2px; }
QSlider::add-page:vertical { background: $primary; border-radius: 2px; }
QSlider::sub-page:vertical { background: $secondary_container; border-radius: 2px; }
QSlider::handle:vertical { height: 18px; width: 18px; margin: 0 -7px; border-radius: 9px; background: $primary; }
QSlider::handle:vertical:hover { background: $primary_hi; }
QSlider::handle:vertical:disabled, QSlider::add-page:vertical:disabled { background: $disabled_container; }
QLabel#eqValue { color: $on_surface; font-size: 12px; font-weight: 600; min-width: 40px; }
QLabel#eqBand { color: $on_surface_variant; font-size: 12px; }
QFrame#eqDivider { background: $outline_variant; }
QLabel#banner { background: $secondary_container; color: $on_secondary_container; border-radius: 12px;
             padding: 12px 16px; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: $outline_variant; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $outline; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: $outline_variant; border-radius: 4px; min-width: 32px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""")


def resolve_dark(app, preset: str, mode: str) -> bool:
    if PRESETS.get(preset, {}).get("force") == "dark":
        return True
    if mode in ("light", "dark"):
        return mode == "dark"
    hints = app.styleHints()
    if hasattr(hints, "unsetColorScheme"):       # forget our own override, read the OS setting
        hints.unsetColorScheme()
    return hints.colorScheme() != Qt.ColorScheme.Light


def apply(app, preset: str = DEFAULT_PRESET, mode: str = "dark", custom_seed: str | None = None) -> bool:
    """Apply a theme to the whole application. Returns True if it ended up dark."""
    global is_dark
    is_dark = resolve_dark(app, preset, mode)
    hints = app.styleHints()
    if mode != "system" and hasattr(hints, "setColorScheme"):   # Qt 6.8+: also themes the title bar
        hints.setColorScheme(Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)
    C.clear()
    C.update(scheme_for(preset, is_dark, custom_seed))

    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Roboto", "Google Sans Text", "Segoe UI Variable Text", "Segoe UI",
                      "Noto Sans", "Cantarell", "sans-serif"])
    font.setPointSize(10)
    app.setFont(font)

    pal = QPalette()
    R = QPalette.ColorRole
    for role, key in ((R.Window, "surface"), (R.WindowText, "on_surface"),
                      (R.Base, "surface_container_low"), (R.AlternateBase, "surface_container"),
                      (R.Text, "on_surface"), (R.Button, "surface_container_high"),
                      (R.ButtonText, "on_surface"), (R.Highlight, "secondary_container"),
                      (R.HighlightedText, "on_secondary_container"), (R.ToolTipBase, "inverse_surface"),
                      (R.ToolTipText, "inverse_on_surface"), (R.PlaceholderText, "on_surface_variant"),
                      (R.Link, "primary"), (R.BrightText, "error"), (R.Mid, "outline_variant"),
                      (R.Dark, "surface_container_lowest"), (R.Light, "surface_container_highest")):
        pal.setColor(role, QColor(C[key]))
    dim = QColor(C["on_surface"])
    dim.setAlphaF(0.38)
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, dim)
    app.setPalette(pal)
    app.setStyleSheet(QSS.substitute(_tokens()))
    return is_dark


# ---- previews for the theme picker ---------------------------------------------------
def preview(s: dict[str, str], w: int = 132, h: int = 88, dpr: float = 2.0) -> QPixmap:
    """A miniature of the app in scheme `s`."""
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)

    def box(x, y, bw, bh, r, key):
        p.setBrush(QColor(s[key]))
        p.drawRoundedRect(QRectF(x, y, bw, bh), r, r)

    box(0, 0, w, h, 12, "surface")
    box(8, 12, 38, 10, 5, "secondary_container")
    box(12, 30, 28, 4, 2, "outline_variant")
    box(12, 40, 22, 4, 2, "outline_variant")
    box(52, 8, w - 60, h - 34, 10, "surface_container_low")
    box(60, 16, 26, 26, 6, "primary_container")
    box(92, 19, w - 108, 5, 2.5, "on_surface")
    box(92, 29, (w - 108) * 0.6, 4, 2, "on_surface_variant")
    box(60, h - 18, w - 68, 3, 1.5, "secondary_container")
    box(60, h - 18, (w - 68) * 0.45, 3, 1.5, "primary")
    box(52 + (w - 52) / 2 - 7, h - 24, 14, 14, 7, "primary")
    p.end()
    return pm


def swatch(s: dict[str, str], size: int = 18) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(1, 1, size - 2, size - 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(s["surface"]))
    p.drawEllipse(r)
    p.setBrush(QColor(s["primary"]))
    p.drawPie(r, 90 * 16, 180 * 16)
    p.setPen(QPen(QColor(s["outline"]), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(r)
    p.end()
    return QIcon(pm)


# ---- icons -------------------------------------------------------------------
# Material Icons (Apache License 2.0), 24x24 viewBox.
PATHS = {
    "play_arrow": "M8 5v14l11-7z",
    "pause": "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    "skip_next": "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
    "skip_previous": "M6 6h2v12H6zm3.5 6l8.5 6V6z",
    "shuffle": "M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z",
    "repeat": "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z",
    "repeat_one": "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z",
    "volume_up": "M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z",
    "volume_down": "M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z",
    "volume_off": "M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z",
    "search": "M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    "library_music": "M20 2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 5h-3v5.5c0 1.38-1.12 2.5-2.5 2.5S10 13.88 10 12.5s1.12-2.5 2.5-2.5c.57 0 1.08.19 1.5.51V5h4v2zM4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6z",
    "album": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 14.5c-2.49 0-4.5-2.01-4.5-4.5S9.51 7.5 12 7.5s4.5 2.01 4.5 4.5-2.01 4.5-4.5 4.5zm0-5.5c-.55 0-1 .45-1 1s.45 1 1 1 1-.45 1-1-.45-1-1-1z",
    "queue_music": "M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z",
    "playlist_add": "M14 10H2v2h12v-2zm0-4H2v2h12V6zm4 8v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zM2 16h8v-2H2v2z",
    "add": "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    "remove": "M19 13H5v-2h14v2z",
    "music_note": "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z",
    "arrow_back": "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    "arrow_upward": "M4 12l1.41 1.41L11 7.83V20h2V7.83l5.58 5.59L20 12l-8-8-8 8z",
    "arrow_downward": "M20 12l-1.41-1.41L13 16.17V4h-2v12.17l-5.58-5.59L4 12l8 8 8-8z",
    "folder": "M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z",
    "create_new_folder": "M20 6h-8l-2-2H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z",
    "delete": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "edit": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "headphones": "M12 1c-4.97 0-9 4.03-9 9v7c0 1.66 1.34 3 3 3h3v-8H5v-2c0-3.87 3.13-7 7-7s7 3.13 7 7v2h-4v8h3c1.66 0 3-1.34 3-3v-7c0-4.97-4.03-9-9-9z",
    "bluetooth": "M17.71 7.71L12 2h-1v7.59L6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 11 14.41V22h1l5.71-5.71-4.3-4.29 4.3-4.29zM13 5.83l1.88 1.88L13 9.59V5.83zm1.88 10.46L13 18.17v-3.76l1.88 1.88z",
    "speaker": "M17 2H7c-1.1 0-2 .9-2 1.99v16c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-5 2c1.1 0 2 .9 2 2s-.9 2-2 2c-1.11 0-2-.9-2-2s.89-2 2-2zm0 16c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z",
    "tv": "M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 1.99-.9 1.99-2L23 5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z",
    "usb": "M15 7v4h1v2h-3V5h2l-3-4-3 4h2v8H8v-2.07c.7-.37 1.2-1.08 1.2-1.93 0-1.21-.99-2.2-2.2-2.2-1.21 0-2.2.99-2.2 2.2 0 .85.5 1.56 1.2 1.93V13c0 1.11.89 2 2 2h3v3.05c-.71.37-1.2 1.1-1.2 1.95 0 1.22.99 2.2 2.2 2.2 1.21 0 2.2-.98 2.2-2.2 0-.85-.49-1.58-1.2-1.95V15h3c1.11 0 2-.89 2-2v-2h1V7h-4z",
    "check": "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
    "settings": "M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z",
    "palette": "M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16c2.76 0 5-2.24 5-5 0-4.42-4.03-8-9-8zm-5.5 9c-.83 0-1.5-.67-1.5-1.5S5.67 9 6.5 9 8 9.67 8 10.5 7.33 12 6.5 12zm3-4C8.67 8 8 7.33 8 6.5S8.67 5 9.5 5s1.5.67 1.5 1.5S10.33 8 9.5 8zm5 0c-.83 0-1.5-.67-1.5-1.5S13.67 5 14.5 5s1.5.67 1.5 1.5S15.33 8 14.5 8zm3 4c-.83 0-1.5-.67-1.5-1.5S16.67 9 17.5 9s1.5.67 1.5 1.5-.67 1.5-1.5 1.5z",
    "equalizer": "M10 20h4V4h-4v16zm-6 0h4v-8H4v8zM16 9v11h4V9h-4z",
    "star": "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z",
    "star_border": "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z",
    "auto_awesome": "M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z",
    "insights": "M21 8c-1.45 0-2.26 1.44-1.93 2.51l-3.55 3.56c-.3-.09-.74-.09-1.04 0l-2.55-2.55C12.27 10.45 11.46 9 10 9c-1.45 0-2.27 1.44-1.93 2.52l-4.56 4.55C2.44 15.74 1 16.55 1 18c0 1.1.9 2 2 2 1.45 0 2.26-1.44 1.93-2.51l4.55-4.56c.3.09.74.09 1.04 0l2.55 2.55C12.73 16.55 13.54 18 15 18c1.45 0 2.27-1.44 1.93-2.52l3.56-3.55c1.07.33 2.51-.48 2.51-1.93 0-1.1-.9-2-2-2z",
    "close": "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    "more_vert": "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    "file_upload": "M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z",
}


def _render(name: str, color: str, px: int) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{color}" d="{PATHS[name]}"/></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def icon(name: str, color: str | None = None, selected: str | None = None) -> QIcon:
    return _icon(name, color or C["on_surface_variant"], selected)


_bound: list = []


def bind(obj, name: str, role: str = "on_surface_variant"):
    """Give a button/action a themed icon that is recoloured on every theme change."""
    obj.setIcon(icon(name, C[role]))
    _bound.append((obj, name, role))
    return obj


def refresh_bound() -> None:
    alive = []
    for obj, name, role in _bound:
        try:
            obj.setIcon(icon(name, C[role]))
            alive.append((obj, name, role))
        except RuntimeError:          # widget already deleted
            pass
    _bound[:] = alive


@lru_cache(maxsize=None)
def _icon(name: str, color: str, selected: str | None) -> QIcon:
    ic = QIcon()
    for px in (24, 48, 96):
        ic.addPixmap(_render(name, color, px), QIcon.Mode.Normal)
        if selected:
            ic.addPixmap(_render(name, selected, px), QIcon.Mode.Selected)
    return ic
