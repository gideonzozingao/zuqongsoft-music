"""Background cover-art thumbnails for the album grid, with an on-disk cache."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage

from .library import read_cover

THUMB = 320   # px, square; drawn smaller in the grid so it stays sharp on HiDPI


class CoverLoader(QThread):
    """Loads/creates thumbnails for [(key, track_path), ...] and emits them one by one."""
    loaded = Signal(str, QImage)

    def __init__(self, items: list[tuple[str, str]], cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.items = items
        self.cache_dir = Path(cache_dir)

    def run(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for key, path in self.items:
            if self.isInterruptionRequested():
                return
            img = self._load(path)
            if img is not None and not img.isNull():
                self.loaded.emit(key, img)

    def _load(self, path: str) -> QImage | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        digest = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{THUMB}".encode()).hexdigest()
        cached, miss = self.cache_dir / f"{digest}.jpg", self.cache_dir / f"{digest}.none"
        if cached.exists():
            return QImage(str(cached))
        if miss.exists():
            return None
        data = read_cover(path)
        if data:
            img = QImage.fromData(data)
            if not img.isNull():
                side = min(img.width(), img.height())          # centre-crop to a square
                img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
                img = img.scaled(THUMB, THUMB, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                img.save(str(cached), "JPG", 88)
                return img
        try:
            miss.touch()
        except OSError:
            pass
        return None
