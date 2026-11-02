"""Navigation sidebar, the songs/albums/stats views, folders and scanning."""
from __future__ import annotations

import os
from PySide6.QtCore import QModelIndex, QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QInputDialog, QListWidgetItem, QWidget
from pathlib import Path
from ..library import SMART, ScanWorker
from ..models import fmt_long
from ..theme import C, icon
from .constants import ALBUMS, SMART_ICONS, SONGS, STATS


class LibraryMixin:
    """Part of MainWindow: navigation sidebar, the songs/albums/stats views, folders and scanning."""

    # ---- navigation ---------------------------------------------------------
    @staticmethod
    def _nav_key(view: dict) -> tuple:
        kind = "albums" if view["kind"] == "album" else view["kind"]
        return kind, view.get("id")

    def _refresh_nav(self) -> None:
        want = self._nav_key(self.view)
        self.nav.blockSignals(True)
        self.nav.clear()

        def add(text, icon_name, view, tip=""):
            it = QListWidgetItem(icon(icon_name, C["on_surface_variant"], C["on_secondary_container"]), text)
            it.setData(Qt.ItemDataRole.UserRole, view)
            it.setSizeHint(QSize(220, 56))
            if tip:
                it.setToolTip(tip)
            self.nav.addItem(it)
            if self._nav_key(view) == want:
                self.nav.setCurrentItem(it)

        def section(text):
            header = QListWidgetItem(text)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setSizeHint(QSize(220, 44))
            f = QFont(self.font())
            f.setWeight(QFont.Weight.DemiBold)
            header.setFont(f)
            self.nav.addItem(header)

        add("Songs", "library_music", SONGS)
        add("Albums", "album", ALBUMS)
        add("Your year in music", "insights", STATS)
        section("Smart playlists")
        for key, (name, *_rest) in SMART.items():
            add(name, SMART_ICONS.get(key, "auto_awesome"), {"kind": "smart", "id": key, "name": name},
                "Updates automatically from your listening")
        section("Playlists")
        playlists = self.library.playlists()
        for pl in playlists:
            add(pl["name"], "queue_music", {"kind": "playlist", "id": pl["id"], "name": pl["name"]},
                f"{pl['tracks']} songs")
        if not playlists:
            hint = QListWidgetItem("Right-click songs to add them")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setSizeHint(QSize(220, 36))
            self.nav.addItem(hint)
        self.nav.blockSignals(False)

    def _on_nav(self, current: QListWidgetItem | None, _previous=None) -> None:
        view = current.data(Qt.ItemDataRole.UserRole) if current else None
        if view:
            self.view = dict(view)
            self._reload()
            self._after_navigation()

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        # Clicking "Albums" while inside an album goes back to the grid.
        view = item.data(Qt.ItemDataRole.UserRole)
        if view and view["kind"] == "albums" and self.view["kind"] == "album":
            self._navigate(ALBUMS)

    def _navigate(self, view: dict) -> None:
        self.view = dict(view)
        self._refresh_nav()
        self._reload()
        self._after_navigation()

    def _open_album(self, album: str, artist: str) -> None:
        self._navigate({"kind": "album", "album": album, "artist": artist})

    def _open_album_index(self, index: QModelIndex) -> None:
        a = index.data(Qt.ItemDataRole.UserRole)
        if a:
            self._open_album(a["album"], a["artist"])

    def _goto_current_album(self) -> None:
        t = self.engine.current_track
        if t:
            self._open_album(t["album"], t.get("album_artist") or t["artist"])

    # ---- library views ------------------------------------------------------
    def _rows_for(self, view: dict, text: str) -> list[dict]:
        kind = view["kind"]
        if kind == "album":
            return self.library.album_tracks(view["album"], view["artist"], text)
        if kind == "playlist":
            return self.library.playlist_tracks(view["id"], text)
        if kind == "smart" and view.get("id") in SMART:
            return self.library.smart_playlist(view["id"], text)
        return self.library.search(text)

    def _reload(self) -> None:
        text = self.search.text()
        view, kind = self.view, self.view["kind"]
        self.btn_back.setVisible(kind == "album")
        if kind == "stats":
            self.view_title.setText("Your year in music")
            self.view_sub.setText("Counted from what you actually listen to")
            self.actions_w.hide()
            self.stats_page.refresh()
            self._show(self.stats_page)
            return
        if kind == "albums":
            albums = self.library.albums(text)
            self.albums.set_albums(albums)
            self.view_title.setText("Albums")
            self.view_sub.setText(f"{len(albums)} albums")
            self.actions_w.hide()
            self._show(self.grid if albums else None, "No albums match your search." if text
                       else "Add a music folder to see your albums here.")
            return

        rows = self._rows_for(view, text)
        self.tracks.set_rows(rows, "track" if kind == "album" else "row")
        total = sum(t["duration_ms"] for t in rows)
        summary = f"{len(rows)} songs, {fmt_long(total)}" if rows else "No songs"
        if kind == "album":
            self.view_title.setText(view["album"])
            self.view_sub.setText(f"{view['artist']}  •  {summary}")
        elif kind in ("playlist", "smart"):
            self.view_title.setText(view["name"])
            self.view_sub.setText(summary + ("  •  updates automatically" if kind == "smart" else ""))
        else:
            self.view_title.setText("Songs")
            self.view_sub.setText(summary)
        self.actions_w.show()
        for b in (self.btn_play_all, self.btn_shuffle_all):
            b.setEnabled(bool(rows))
        if rows:
            self._show(self.table)
        elif text:
            self._show(None, "No songs match your search.")
        elif kind == "playlist":
            self._show(None, "This playlist is empty.\nRight-click any song and choose Add to playlist.")
        elif kind == "smart":
            self._show(None, {"most_played": "Nothing played yet. Songs appear here as you listen.",
                              "recently_played": "Nothing played yet.",
                              "top_rated": "No songs rated 4 or 5 stars yet.\nClick the stars in the Rating column.",
                              "forgotten": "No forgotten favourites: songs you played 3+ times\n"
                                           "but not in the last 2 months show up here.",
                              }.get(view["id"], "No songs here yet."))
        else:
            self._show(None, "Your library is empty.\nAdd a music folder (Ctrl+O) or drop one on this window.")

    def _show(self, widget: QWidget | None, empty_text: str = "") -> None:
        if widget is None:
            self.empty.setText(empty_text)
            widget = self.empty
        self.stack.setCurrentWidget(widget)

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self.settings.setValue("sort/column", column)
        self.settings.setValue("sort/order", order.value)

    # ---- folders / scanning ---------------------------------------------------
    def _pick_folder(self) -> None:
        start = (self._folders() or [str(Path.home() / "Music")])[-1]
        path = QFileDialog.getExistingDirectory(self, "Add music folder", start)
        if path:
            self._add_folder(path)

    def _add_folder(self, path: str) -> None:
        path = os.path.normpath(path)
        folders = self._folders()
        if path not in folders:
            folders.append(path)
            self.settings.setValue("folders", folders)
        self._rescan()

    def _remove_folder(self) -> None:
        folders = self._folders()
        if not folders:
            self.statusBar().showMessage("No folders in the library yet.", 4000)
            return
        path, ok = QInputDialog.getItem(self, "Remove folder", "Folder to remove:", folders, 0, False)
        if ok and path:
            folders.remove(path)
            self.settings.setValue("folders", folders)
            self.library.remove_under(path)
            self.covers.clear()
            self._refresh_nav()
            self._reload()

    def _rescan(self) -> None:
        folders = self._folders()
        if not folders:
            self.statusBar().showMessage("Add a music folder with File > Add Music Folder (Ctrl+O).")
            return
        if self.scanner is not None and self.scanner.isRunning():
            self._rescan_pending = True
            return
        self.scanner = ScanWorker(self.library, folders, self)
        self.scanner.progress.connect(lambda n: self.statusBar().showMessage(f"Scanning… {n} files"))
        self.scanner.finished.connect(self._scan_finished)
        self.scanner.start()

    def _scan_finished(self) -> None:
        w = self.scanner
        if w is not None:
            self.statusBar().showMessage(f"Library updated: {w.changed} new or changed, {w.removed} removed", 6000)
            if w.changed and (self.settings.value("sound/level", False, type=bool)
                              or self.settings.value("dj/enabled", False, type=bool)):
                self.start_loudness_scan()
            if w.changed or w.removed:
                self.covers.clear()
                self._refresh_nav()
                self._reload()
        if self._rescan_pending:
            self._rescan_pending = False
            self._rescan()

    # ---- drag & drop a folder onto the window -----------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                self._add_folder(p)
