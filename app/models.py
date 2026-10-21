"""Qt item models: sortable track table, album grid model + card delegate, async cover cache."""
from __future__ import annotations

from collections import OrderedDict

import math
import time

from PySide6.QtCore import (QAbstractListModel, QAbstractTableModel, QEvent, QModelIndex, QObject,
                            QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap, QPolygonF
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .library import read_cover
from .theme import C, icon


def fmt(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_long(ms: int) -> str:
    minutes = round(max(0, ms) / 60000)
    h, m = divmod(minutes, 60)
    return f"{h} hr {m} min" if h else f"{m} min"


# ---- sorting ------------------------------------------------------------------
def _cf(s) -> str:
    return (s or "").casefold()


COL_NUM, COL_TITLE, COL_ARTIST, COL_ALBUM, COL_RATING, COL_TIME = range(6)
SORT_KEYS = {
    COL_TITLE: lambda t: _cf(t["title"]),
    COL_ARTIST: lambda t: (_cf(t["artist"]), _cf(t["album"]), t.get("disc_no", 0), t["track_no"]),
    COL_ALBUM: lambda t: (_cf(t["album"]), t.get("disc_no", 0), t["track_no"]),
    COL_RATING: lambda t: (t.get("rating", 0), t.get("play_count", 0)),
    COL_TIME: lambda t: t["duration_ms"],
}


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    d = max(0, time.time() - ts)
    for unit, secs in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def star_polygon(cx: float, cy: float, r: float) -> QPolygonF:
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return QPolygonF(pts)


def paint_stars(p: QPainter, rect: QRectF, rating: int, size: float = 16, gap: float = 3,
                on: str | None = None, off: str | None = None) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    y = rect.center().y()
    for i in range(5):
        cx = rect.left() + size / 2 + i * (size + gap)
        p.setBrush(QColor(on or C["primary"]) if i < rating else QColor(off or C["outline_variant"]))
        p.drawPolygon(star_polygon(cx, y, size / 2))
    p.restore()


def star_at(x: float, left: float, size: float = 16, gap: float = 3) -> int:
    """1..5 for a click at x, 0 when left of the first star."""
    if x < left:
        return 0
    return max(1, min(5, int((x - left) // (size + gap)) + 1))


def sort_rows(rows: list[dict], column: int, order: Qt.SortOrder) -> list[dict]:
    """Column 0 ("#") is the view's natural order: artist -> album -> track for the
    library, track order for an album, the user's order for a playlist."""
    desc = order == Qt.SortOrder.DescendingOrder
    if column in SORT_KEYS:
        return sorted(rows, key=SORT_KEYS[column], reverse=desc)   # stable: ties keep natural order
    return list(reversed(rows)) if desc else list(rows)


class TrackModel(QAbstractTableModel):
    HEADERS = ["#", "Title", "Artist", "Album", "Rating", "Time"]

    def __init__(self) -> None:
        super().__init__()
        self._base: list[dict] = []
        self._rows: list[dict] = []
        self._playing_id: int | None = None
        self._sort = (0, Qt.SortOrder.AscendingOrder)
        self.number_mode = "row"            # "row" or "track"
        self._bold = QFont()
        self._bold.setBold(True)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t, col = self._rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return (t["track_no"] or "") if self.number_mode == "track" else index.row() + 1
            return (None, t["title"], t["artist"], t["album"], None, fmt(t["duration_ms"]))[col]
        if role == Qt.ItemDataRole.UserRole and col == COL_RATING:
            return t.get("rating", 0)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_RATING:
                return "Click a star to rate (click it again to clear)"
            plays = t.get("play_count", 0)
            return (f"{t['path']}\nPlayed {plays} time{'s' if plays != 1 else ''}"
                    f", last played {ago(t.get('last_played'))}")
        playing = t["id"] == self._playing_id
        if role == Qt.ItemDataRole.FontRole and playing:
            return self._bold
        if role == Qt.ItemDataRole.ForegroundRole and playing:
            return QColor(C["primary"])
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_NUM, COL_TIME):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and section in (COL_NUM, COL_TIME):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self.beginResetModel()
        self._sort = (column, order)
        self._rows = sort_rows(self._base, column, order)
        self.endResetModel()

    def sort_state(self) -> tuple[int, Qt.SortOrder]:
        return self._sort

    def natural_order(self) -> bool:
        return self._sort == (0, Qt.SortOrder.AscendingOrder)

    def set_rows(self, rows: list[dict], number_mode: str = "row") -> None:
        self.beginResetModel()
        self._base = rows
        self.number_mode = number_mode
        self._rows = sort_rows(rows, *self._sort)
        self.endResetModel()

    def tracks(self) -> list[dict]:
        return self._rows

    def track(self, row: int) -> dict:
        return self._rows[row]

    def set_playing(self, track_id: int | None) -> None:
        self._playing_id = track_id
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(self.HEADERS) - 1))

    def set_rating(self, row: int, rating: int) -> None:
        self._rows[row]["rating"] = rating      # dicts are shared with the queue, so this sticks
        idx = self.index(row, COL_RATING)
        self.dataChanged.emit(idx, idx)

    def row_of(self, track_id: int) -> int:
        for i, t in enumerate(self._rows):
            if t["id"] == track_id:
                return i
        return -1


class StarDelegate(QStyledItemDelegate):
    """Five clickable stars in the Rating column."""
    rated = Signal(int, int)        # row, rating
    LEFT = 12

    def paint(self, p: QPainter, option, index) -> None:
        super().paint(p, option, index)            # background / selection
        rating = index.data(Qt.ItemDataRole.UserRole) or 0
        r = QRectF(option.rect).adjusted(self.LEFT, 0, 0, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        paint_stars(p, r, rating, 14, 2, on=C["on_secondary_container"] if selected else C["primary"])

    def sizeHint(self, option, index) -> QSize:
        return QSize(self.LEFT + 5 * 16 + 12, 40)

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            stars = star_at(event.position().x(), option.rect.left() + self.LEFT, 14, 2)
            if stars:
                current = index.data(Qt.ItemDataRole.UserRole) or 0
                self.rated.emit(index.row(), 0 if stars == current else stars)
                return True
        return False


# ---- covers -------------------------------------------------------------------
class _CoverSignals(QObject):
    done = Signal(str, QImage)


class _CoverJob(QRunnable):
    def __init__(self, path: str, px: int, signals: _CoverSignals) -> None:
        super().__init__()
        self.path, self.px, self.signals = path, px, signals

    def run(self) -> None:
        img = QImage()
        data = read_cover(self.path)
        if data and img.loadFromData(data):
            img = img.scaled(self.px, self.px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            img = img.copy((img.width() - self.px) // 2, (img.height() - self.px) // 2, self.px, self.px)
        else:
            img = QImage()
        self.signals.done.emit(self.path, img)


class CoverCache(QObject):
    """Loads album art off the UI thread; `changed` fires (coalesced) as covers arrive."""
    changed = Signal()

    def __init__(self, px: int = 352, limit: int = 800, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.px, self.limit = px, limit
        self._items: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: set[str] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._signals = _CoverSignals(self)
        self._signals.done.connect(self._done)
        self._notify = QTimer(self, singleShot=True, interval=40)
        self._notify.timeout.connect(self.changed)

    def get(self, path: str) -> QPixmap | None:
        """Cached pixmap (null pixmap = no art), or None while it is loading."""
        if path in self._items:
            self._items.move_to_end(path)
            return self._items[path]
        if path not in self._pending:
            self._pending.add(path)
            self._pool.start(_CoverJob(path, self.px, self._signals))
        return None

    def _done(self, path: str, img: QImage) -> None:
        self._pending.discard(path)
        self._items[path] = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        while len(self._items) > self.limit:
            self._items.popitem(last=False)
        self._notify.start()

    def clear(self) -> None:
        self._items.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)


# ---- albums -------------------------------------------------------------------
class AlbumModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self._albums: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._albums)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        a = self._albums[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return a["album"]
        if role == Qt.ItemDataRole.UserRole:
            return a
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{a['album']}\n{a['artist']}\n{a['tracks']} songs, {fmt_long(a['duration_ms'])}"
        return None

    def set_albums(self, albums: list[dict]) -> None:
        self.beginResetModel()
        self._albums = albums
        self.endResetModel()


class AlbumDelegate(QStyledItemDelegate):
    """M3 card: rounded container, cover art, title and artist."""
    SIZE = QSize(188, 252)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.covers = covers

    def sizeHint(self, option, index) -> QSize:
        return self.SIZE

    def paint(self, p: QPainter, option, index) -> None:
        a = index.data(Qt.ItemDataRole.UserRole)
        if not a:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        r = QRectF(option.rect).adjusted(4, 4, -4, -4)
        bg = C["secondary_container"] if selected else (C["surface_container_high"] if hovered else C["surface_container"])
        card = QPainterPath()
        card.addRoundedRect(r, 16, 16)
        p.fillPath(card, QColor(bg))

        side = r.width() - 16
        cover = QRectF(r.left() + 8, r.top() + 8, side, side)
        clip = QPainterPath()
        clip.addRoundedRect(cover, 12, 12)
        p.save()
        p.setClipPath(clip)
        pix = self.covers.get(a["path"])
        if pix is not None and not pix.isNull():
            p.drawPixmap(cover, pix, QRectF(pix.rect()))
        else:
            p.fillRect(cover, QColor(C["surface_container_highest"]))
            m = side * 0.32
            icon("album", C["outline"]).paint(p, cover.adjusted(m, m, -m, -m).toRect())
        p.restore()

        fg = C["on_secondary_container"] if selected else C["on_surface"]
        text_w = int(r.width() - 24)
        f = QFont(option.font)
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(fg))
        title = QRect(int(r.left() + 12), int(cover.bottom() + 10), text_w, 20)
        p.drawText(title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(a["album"], Qt.TextElideMode.ElideRight, text_w))
        f2 = QFont(option.font)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(C["on_surface_variant"]))
        sub = QRect(title.left(), title.bottom() + 2, text_w, 18)
        p.drawText(sub, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(a["artist"], Qt.TextElideMode.ElideRight, text_w))
        p.restore()
