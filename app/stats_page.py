"""'Your year in music': listening totals, top songs/artists/albums and a month-by-month chart."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .theme import C, icon

MONTHS = "JFMAMJJASOND"
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def _minutes(ms: int) -> str:
    return f"{round(ms / 60000):,}"


def _duration(ms: int) -> str:
    m = round(ms / 60000)
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if h else f"{m} min"


class MonthBars(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.months: dict[int, int] = {}
        self.setMinimumHeight(170)
        self.setMouseTracking(True)

    def set_data(self, months: dict[int, int]) -> None:
        self.months = months
        self.update()

    def _bar_rect(self, i: int, top: float, h: float) -> QRectF:
        slot = self.width() / 12
        return QRectF(i * slot + slot * 0.2, top, slot * 0.6, h)

    def mouseMoveEvent(self, e) -> None:
        i = int(e.position().x() // (self.width() / 12))
        if 0 <= i < 12:
            self.setToolTip(f"{MONTH_NAMES[i]}: {_duration(self.months.get(i + 1, 0))}")

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_h = 22
        chart_h = self.height() - label_h - 6
        peak = max(self.months.values(), default=0) or 1
        best = max(self.months, key=self.months.get) if self.months else None
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        for i in range(12):
            ms = self.months.get(i + 1, 0)
            h = max(4.0, chart_h * ms / peak) if ms else 4.0
            r = self._bar_rect(i, chart_h - h, h)
            path = QPainterPath()
            path.addRoundedRect(r, min(8, r.width() / 2), min(8, r.width() / 2))
            colour = QColor(C["primary"]) if i + 1 == best else QColor(C["primary"] if ms else C["outline_variant"])
            if i + 1 != best and ms:
                colour.setAlphaF(0.45)
            p.fillPath(path, colour)
            p.setPen(QColor(C["on_surface"] if i + 1 == best else C["on_surface_variant"]))
            p.drawText(QRectF(r.left() - 10, chart_h + 6, r.width() + 20, label_h),
                       Qt.AlignmentFlag.AlignCenter, MONTHS[i])


class AlbumTile(QPushButton):
    """Cover art + caption; repaints when the cover cache delivers the image."""

    def __init__(self, album: dict, covers, parent=None) -> None:
        super().__init__(parent)
        self.album, self.covers = album, covers
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(150, 206))
        self.setToolTip(f"{album['album']}\n{album['artist']}\n{_duration(album['ms'])} this year")
        covers.changed.connect(self.update)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = self.width()
        cover = QRectF(0, 0, side, side)
        clip = QPainterPath()
        clip.addRoundedRect(cover, 16, 16)
        p.save()
        p.setClipPath(clip)
        pix = self.covers.get(self.album["path"])
        if pix is not None and not pix.isNull():
            p.drawPixmap(cover, pix, QRectF(pix.rect()))
        else:
            p.fillRect(cover, QColor(C["surface_container_highest"]))
            icon("album", C["outline"]).paint(p, cover.adjusted(45, 45, -45, -45).toRect())
        if self.underMouse():
            hover = QColor(C["on_surface"])
            hover.setAlphaF(0.08)
            p.fillRect(cover, hover)
        p.restore()
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["on_surface"]))
        fm = QFontMetrics(f)
        p.drawText(QRectF(2, side + 8, side - 4, 18), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.album["album"], Qt.TextElideMode.ElideRight, side - 4))
        f.setWeight(QFont.Weight.Normal)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(C["on_surface_variant"]))
        p.drawText(QRectF(2, side + 28, side - 4, 18), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(f).elidedText(self.album["artist"], Qt.TextElideMode.ElideRight, side - 4))


def _card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(objectName="card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(8)
    if title:
        lay.addWidget(QLabel(title, objectName="cardTitle"))
    return card, lay


class StatsPage(QScrollArea):
    playPaths = Signal(list, int)       # paths, start index
    openAlbum = Signal(str, str)

    def __init__(self, library, covers, parent=None) -> None:
        super().__init__(parent)
        self.library, self.covers = library, covers
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.year: int | None = None
        self._body: QWidget | None = None

    def refresh(self) -> None:
        years = self.library.years_with_plays()
        if self.year not in years:
            self.year = years[0] if years else None
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 16)
        lay.setSpacing(16)
        if self.year is None:
            msg = QLabel("No listening history yet.\n\nA song counts as played once you've heard half of "
                         "it (or 4 minutes).\nYour year in music fills in as you listen.", objectName="empty")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addStretch(1)
            lay.addWidget(msg)
            lay.addStretch(2)
            self._swap(body)
            return
        s = self.library.year_summary(self.year)

        top = QHBoxLayout()
        pick = QComboBox()
        for y in years:
            pick.addItem(f"{y} so far" if y == dt.date.today().year else str(y), y)
        pick.setCurrentIndex(years.index(self.year))
        pick.currentIndexChanged.connect(lambda i: self._pick_year(pick.itemData(i)))
        top.addWidget(pick)
        top.addStretch(1)
        if s["top_tracks"]:
            play = QPushButton("Play your top songs")
            play.setProperty("kind", "filled")
            play.setIcon(icon("play_arrow", C["on_primary"]))
            play.setCursor(Qt.CursorShape.PointingHandCursor)
            paths = [t["path"] for t in s["top_tracks"]]
            play.clicked.connect(lambda: self.playPaths.emit(paths, 0))
            top.addWidget(play)
        lay.addLayout(top)

        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        for value, label in ((_minutes(s["ms"]), "minutes listened"), (f"{s['plays']:,}", "play" if s["plays"] == 1 else "plays"),
                             (f"{s['songs']:,}", "different songs"), (f"{s['artists']:,}", "artists")):
            card, cl = _card()
            v = QLabel(value, objectName="kpi")
            cl.addWidget(v)
            cl.addWidget(QLabel(label, objectName="muted"))
            kpis.addWidget(card, 1)
        lay.addLayout(kpis)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        hero, hl = _card("Top artist")
        if s["top_artists"]:
            a = s["top_artists"][0]
            name = QLabel(a["artist"], objectName="hero")
            name.setWordWrap(True)
            hl.addWidget(name)
            hl.addWidget(QLabel(f"{_duration(a['ms'])}  ·  {_n(a['plays'], 'play')}", objectName="muted"))
            for i, other in enumerate(s["top_artists"][1:], start=2):
                hl.addWidget(QLabel(f"{i}.  {other['artist']}   ·   {_duration(other['ms'])}"))
        hl.addStretch(1)
        mid.addWidget(hero, 2)
        chart, chl = _card("Listening by month")
        bars = MonthBars()
        bars.set_data(s["months"])
        chl.addWidget(bars, 1)
        if s["best_day"]:
            day = dt.date.fromisoformat(s["best_day"]["day"])
            chl.addWidget(QLabel(f"Biggest day: {day.day} {MONTH_NAMES[day.month - 1]}, "
                                 f"{_duration(s['best_day']['ms'])}", objectName="muted"))
        mid.addWidget(chart, 3)
        lay.addLayout(mid)

        songs, sl = _card("Top songs")
        paths = [t["path"] for t in s["top_tracks"]]
        for i, t in enumerate(s["top_tracks"]):
            row = QPushButton(objectName="listRow")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setToolTip("Play from here")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 4, 12, 4)
            rank = QLabel(str(i + 1), objectName="rank")
            rank.setFixedWidth(28)
            title = QLabel(f"<b>{_esc(t['title'])}</b>  <span style='color:{C['on_surface_variant']}'>"
                           f"{_esc(t['artist'])}</span>")
            title.setTextFormat(Qt.TextFormat.RichText)
            plays = QLabel(_n(t["plays"], "play"), objectName="muted")
            for w in (rank, title, plays):
                w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            rl.addWidget(rank)
            rl.addWidget(title, 1)
            rl.addWidget(plays)
            row.clicked.connect(lambda _c=False, k=i: self.playPaths.emit(paths, k))
            sl.addWidget(row)
        lay.addWidget(songs)

        if s["top_albums"]:
            albums, al = _card("Top albums")
            grid = QHBoxLayout()
            grid.setSpacing(16)
            for a in s["top_albums"]:
                tile = AlbumTile(a, self.covers)
                tile.clicked.connect(lambda _c=False, a=a: self.openAlbum.emit(a["album"], a["artist"]))
                grid.addWidget(tile)
            grid.addStretch(1)
            al.addLayout(grid)
            lay.addWidget(albums)
        lay.addStretch(1)
        self._swap(body)

    def _pick_year(self, year: int) -> None:
        self.year = year
        self.refresh()

    def _swap(self, body: QWidget) -> None:
        old = self.takeWidget()
        self.setWidget(body)
        if old is not None:
            old.deleteLater()


def _n(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _esc(s: str) -> str:
    import html
    return html.escape(s)
