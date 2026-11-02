"""Playlists, context menus, queueing and ratings."""
from __future__ import annotations

import os
import subprocess
import sys
from PySide6.QtCore import QModelIndex, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox
from pathlib import Path
from ..library import read_m3u, write_m3u
from ..theme import icon
from .constants import SONGS
from .widgets import add_action


class PlaylistMixin:
    """Part of MainWindow: playlists, context menus, queueing and ratings."""

    # ---- playlists ------------------------------------------------------------
    def _selected_tracks(self) -> list[dict]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.tracks.track(r) for r in rows]

    def _ask_name(self, title: str, default: str) -> str:
        name, ok = QInputDialog.getText(self, title, "Name", QLineEdit.EchoMode.Normal, default)
        return name.strip() if ok else ""

    def _new_playlist(self, paths: list[str] | None = None) -> None:
        existing = {p["name"] for p in self.library.playlists()}
        default, n = "New playlist", 2
        while default in existing:
            default, n = f"New playlist {n}", n + 1
        name = self._ask_name("New playlist", default)
        if not name:
            return
        pid = self.library.create_playlist(name)
        if paths:
            added = self.library.add_to_playlist(pid, paths)
            self.statusBar().showMessage(f"Created “{name}” with {added} songs", 5000)
            self._refresh_nav()
        else:
            self._navigate({"kind": "playlist", "id": pid, "name": name})

    def _add_to_playlist(self, pid: int, name: str, paths: list[str]) -> None:
        added = self.library.add_to_playlist(pid, paths)
        skipped = len(paths) - added
        msg = f"Added {added} song{'s' if added != 1 else ''} to “{name}”"
        if skipped:
            msg += f" ({skipped} already there)"
        self.statusBar().showMessage(msg, 5000)
        self._refresh_nav()
        if self.view.get("kind") == "playlist" and self.view.get("id") == pid:
            self._reload()

    def _remove_selected_from_playlist(self) -> None:
        if self.view["kind"] != "playlist":
            return
        sel = self._selected_tracks()
        if sel:
            self.library.remove_entries(self.view["id"], [t["entry_id"] for t in sel])
            self._refresh_nav()
            self._reload()

    def _move_entry(self, track: dict, delta: int) -> None:
        if self.library.move_entry(self.view["id"], track["entry_id"], delta):
            self._reload()
            for row, t in enumerate(self.tracks.tracks()):
                if t["entry_id"] == track["entry_id"]:
                    self.table.selectRow(row)
                    break

    def _rename_playlist(self, view: dict) -> None:
        name = self._ask_name("Rename playlist", view["name"])
        if name and name != view["name"]:
            self.library.rename_playlist(view["id"], name)
            if self.view.get("kind") == "playlist" and self.view.get("id") == view["id"]:
                self.view["name"] = name
            if self._queue_source.get("id") == view["id"]:
                self._queue_source["name"] = name
            self._refresh_nav()
            self._reload()

    def _delete_playlist(self, view: dict) -> None:
        answer = QMessageBox.question(self, "Delete playlist",
                                      f"Delete “{view['name']}”? The songs stay in your library.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_playlist(view["id"])
        if self.view.get("kind") == "playlist" and self.view.get("id") == view["id"]:
            self.view = dict(SONGS)
        self._refresh_nav()
        self._reload()

    def _export_playlist(self, view: dict) -> None:
        suggested = str(Path.home() / f"{view['name']}.m3u8")
        path, _ = QFileDialog.getSaveFileName(self, "Export playlist", suggested, "M3U playlist (*.m3u8 *.m3u)")
        if path:
            rows = self.library.playlist_tracks(view["id"])
            write_m3u(path, rows)
            self.statusBar().showMessage(f"Exported {len(rows)} songs to {path}", 6000)

    def _import_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import playlist", str(Path.home()),
                                              "M3U playlist (*.m3u8 *.m3u)")
        if not path:
            return
        try:
            paths = read_m3u(path)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not read {path}: {exc}", 8000)
            return
        name = Path(path).stem
        pid = self.library.create_playlist(name)
        self.library.add_to_playlist(pid, paths)
        found = len(self.library.playlist_tracks(pid))
        self._navigate({"kind": "playlist", "id": pid, "name": name})
        missing = len(paths) - found
        self.statusBar().showMessage(
            f"Imported “{name}”: {found} songs" + (f", {missing} not in your library yet" if missing else ""), 8000)

    def _playlist_submenu(self, menu: QMenu, paths: list[str]) -> None:
        sub = menu.addMenu(icon("playlist_add"), "Add to playlist")
        add_action(sub, "add", "New playlist…", lambda: self._new_playlist(paths))
        playlists = self.library.playlists()
        if playlists:
            sub.addSeparator()
        for pl in playlists:
            add_action(sub, "queue_music", pl["name"],
                       lambda pid=pl["id"], name=pl["name"]: self._add_to_playlist(pid, name, paths))

    # ---- context menus ----------------------------------------------------------
    def _track_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        if not self.table.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            self.table.selectRow(idx.row())
        sel = self._selected_tracks()
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_rows(self.tracks.tracks(), idx.row()))
        if len(sel) == 1:
            add_action(m, "radio", "Start radio from this song", lambda: self.start_radio(sel[0]))
        self._queue_actions(m, sel)
        m.addSeparator()
        self._playlist_submenu(m, [t["path"] for t in sel])
        self._rate_submenu(m, sel)
        if len(sel) == 1 and self.engine.supports_eq:
            self._auto_eq_menu(m, sel[0])
        if self.view["kind"] == "playlist":
            can_move = len(sel) == 1 and self.tracks.natural_order() and not self.search.text()
            m.addSeparator()
            add_action(m, "arrow_upward", "Move up", lambda: self._move_entry(sel[0], -1), can_move)
            add_action(m, "arrow_downward", "Move down", lambda: self._move_entry(sel[0], 1), can_move)
            add_action(m, "remove", "Remove from playlist", self._remove_selected_from_playlist)
        if len(sel) == 1:
            t = sel[0]
            m.addSeparator()
            if self.view["kind"] != "album":
                add_action(m, "album", "Go to album",
                           lambda: self._open_album(t["album"], t.get("album_artist") or t["artist"]))
            add_action(m, "folder", "Show in folder", lambda: self._reveal(t["path"]))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _album_menu(self, pos) -> None:
        idx = self.grid.indexAt(pos)
        a = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if not a:
            return
        view = {"kind": "album", "album": a["album"], "artist": a["artist"]}
        rows = self.library.album_tracks(a["album"], a["artist"])
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_source(view, rows, False))
        add_action(m, "shuffle", "Shuffle", lambda: self._play_source(view, rows, True))
        add_action(m, "album", "Open", lambda: self._open_album(a["album"], a["artist"]))
        self._queue_actions(m, rows)
        m.addSeparator()
        self._playlist_submenu(m, [t["path"] for t in rows])
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _nav_menu(self, pos) -> None:
        item = self.nav.itemAt(pos)
        view = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not view or view["kind"] not in ("playlist", "smart"):
            return
        rows = self._rows_for(view, "")
        m = QMenu(self)
        add_action(m, "play_arrow", "Play", lambda: self._play_source(view, rows, False), bool(rows))
        add_action(m, "shuffle", "Shuffle", lambda: self._play_source(view, rows, True), bool(rows))
        self._queue_actions(m, rows)
        if view["kind"] == "smart":
            m.exec(self.nav.viewport().mapToGlobal(pos))
            return
        m.addSeparator()
        add_action(m, "edit", "Rename…", lambda: self._rename_playlist(view))
        add_action(m, "file_upload", "Export as M3U…", lambda: self._export_playlist(view), bool(rows))
        m.addSeparator()
        add_action(m, "delete", "Delete", lambda: self._delete_playlist(view))
        m.exec(self.nav.viewport().mapToGlobal(pos))

    def _queue_actions(self, menu: QMenu, tracks: list[dict]) -> None:
        n = len(tracks)
        add_action(menu, "skip_next", "Play next", lambda: self._enqueue(tracks, True), n > 0)
        add_action(menu, "queue_music", "Add to queue", lambda: self._enqueue(tracks, False), n > 0)

    def _enqueue(self, tracks: list[dict], play_next: bool) -> None:
        self.engine.enqueue(tracks, play_next)
        what = f"“{tracks[0]['title']}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        self.statusBar().showMessage(f"{what} will play next" if play_next else f"Added {what} to the queue", 4000)

    def _rate_submenu(self, menu: QMenu, tracks: list[dict]) -> None:
        sub = menu.addMenu(icon("star"), "Rate")
        current = tracks[0].get("rating", 0) if len(tracks) == 1 else -1
        for r in range(5, -1, -1):
            act = sub.addAction("★" * r + "☆" * (5 - r) if r else "No rating")
            act.setCheckable(True)
            act.setChecked(r == current)
            act.triggered.connect(lambda _c=False, r=r: self._rate_tracks(tracks, r))

    def _rate_tracks(self, tracks: list[dict], rating: int) -> None:
        for t in tracks:
            self.library.set_rating(t["path"], rating)
            t["rating"] = rating
        self.tracks.layoutChanged.emit()
        self._refresh_now_playing_stats()

    def _rate_row(self, row: int, rating: int) -> None:
        t = self.tracks.track(row)
        self.library.set_rating(t["path"], rating)
        self.tracks.set_rating(row, rating)
        cur = self.engine.current_track
        if cur and cur["path"] == t["path"]:
            cur["rating"] = rating
            self._refresh_now_playing_stats()

    def _rate_current(self, rating: int) -> None:
        cur = self.engine.current_track
        if cur:
            self._rate_tracks([cur], rating)
            row = self.tracks.row_of(cur["id"])
            if row >= 0:
                self.tracks.set_rating(row, rating)

    def _refresh_now_playing_stats(self) -> None:
        cur = self.engine.current_track
        self.stars.setEnabled(cur is not None)
        self.stars.set_rating(cur.get("rating", 0) if cur else 0)
        if cur:
            n = cur.get("play_count", 0)
            self.plays_lbl.setText(f"Played {n} time{'s' if n != 1 else ''}" if n else "Not played before")
        else:
            self.plays_lbl.setText("")

    def _on_play_recorded(self, path: str, counted: bool) -> None:
        self._refresh_now_playing_stats()
        if self.view["kind"] == "stats":
            self.stats_page.refresh()

    def _play_paths(self, paths: list[str], start: int) -> None:
        rows = self.library.tracks_by_paths(paths)
        if rows:
            start_path = paths[start] if 0 <= start < len(paths) else None
            idx = next((i for i, t in enumerate(rows) if t["path"] == start_path), 0)
            self._play_source(SONGS, rows, False, idx)

    @staticmethod
    def _reveal(path: str) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
