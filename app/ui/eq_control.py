"""The equalizer: per-device profiles, automatic EQ rules and the EQ window."""
from __future__ import annotations

import json
import sys
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMenu
from .. import BUNDLED
from ..equalizer import PRESETS as EQ_PRESETS
from ..equalizer_ui import EqualizerDialog
from ..theme import icon


class EqualizerMixin:
    """Part of MainWindow: the equalizer: per-device profiles, automatic EQ rules and the EQ window."""

    def _load_eq_for_device(self, force: bool = False) -> None:
        """With per-device profiles, switching output switches the EQ curve too."""
        if not hasattr(self, "eq_store"):
            return
        dev = self._out_id()
        if not force and (not self.eq_store.per_device or dev == self._eq_device):
            return
        self._eq_device = dev
        self.eq = self.eq_store.load(dev, fallback=None if force else self.eq)
        self.apply_eq()
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    def apply_eq(self) -> None:
        self.engine.set_equalizer(self._auto_eq[1] if self._auto_eq else self.eq)
        self._refresh_eq_button()

    # ---- automatic EQ (per genre / album / song) --------------------------------------
    def auto_rules(self) -> list[dict]:
        try:
            rules = json.loads(self.settings.value("eq/auto_rules", "") or "[]")
        except (TypeError, ValueError):
            rules = []
        return [r for r in rules if isinstance(r, dict) and r.get("kind") in ("song", "album", "genre")]

    def set_auto_rules(self, rules: list[dict]) -> None:
        self.settings.setValue("eq/auto_rules", json.dumps(rules))
        self._apply_auto_eq(self.engine.current_track)
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.sync()

    @staticmethod
    def _album_key(t: dict) -> str:
        return f"{t.get('album', '')}\x1f{t.get('album_artist') or t.get('artist', '')}"

    def _rule_for(self, t: dict | None) -> dict | None:
        if not t:
            return None
        rules = self.auto_rules()
        for kind, key in (("song", t.get("path", "")), ("album", self._album_key(t)),
                          ("genre", (t.get("genre") or "").casefold())):
            for r in rules:
                if r["kind"] == kind and key and (r["key"].casefold() if kind == "genre" else r["key"]) == key:
                    return r
        return None

    def _preset_gains(self, name: str) -> list[float] | None:
        if name in EQ_PRESETS:
            return [float(g) for g in EQ_PRESETS[name]]
        user = self.eq_store.user_presets().get(name)
        return [float(g) for g in user] if user else None

    def _apply_auto_eq(self, t: dict | None) -> None:
        rule = self._rule_for(t)
        gains = self._preset_gains(rule["preset"]) if rule else None
        if rule and gains is not None:
            override = self.eq.copy()
            override.enabled, override.mode, override.gains, override.preset = True, "simple", gains, rule["preset"]
            changed = not self._auto_eq or self._auto_eq[0] != rule
            self._auto_eq = (rule, override)
            self.apply_eq()
            if changed:
                self.statusBar().showMessage(f"Automatic EQ: {rule['preset']} (for {rule['label']})", 5000)
        elif self._auto_eq:
            self._auto_eq = None
            self.apply_eq()

    def clear_auto_eq(self) -> None:
        """Editing the EQ by hand takes over from an automatic rule for the current song."""
        self._auto_eq = None

    def add_auto_rule(self, kind: str, key: str, label: str, preset: str | None) -> None:
        rules = [r for r in self.auto_rules() if not (r["kind"] == kind and r["key"] == key)]
        if preset:
            rules.append({"kind": kind, "key": key, "label": label, "preset": preset})
        self.set_auto_rules(rules)

    def _auto_eq_menu(self, menu: QMenu, t: dict) -> None:
        sub = menu.addMenu(icon("equalizer"), "Automatic EQ")
        presets = list(EQ_PRESETS) + sorted(self.eq_store.user_presets(), key=str.casefold)
        current = {r["kind"]: r for r in self.auto_rules()
                   if r["key"] in (t["path"], self._album_key(t), (t.get("genre") or "").casefold())
                   or (r["kind"] == "genre" and r["key"].casefold() == (t.get("genre") or "").casefold())}
        targets = [("song", t["path"], f"“{t['title']}”", "For this song"),
                   ("album", self._album_key(t), f"the album {t['album']}", "For this album")]
        if t.get("genre"):
            targets.append(("genre", t["genre"].casefold(), f"genre {t['genre']}", f"For genre “{t['genre']}”"))
        for kind, key, label, text in targets:
            m = sub.addMenu(text)
            now = current.get(kind)
            for name in presets:
                a = m.addAction(name)
                a.setCheckable(True)
                a.setChecked(bool(now and now["preset"] == name))
                a.triggered.connect(lambda _c=False, k=kind, ky=key, lb=label, n=name: self.add_auto_rule(k, ky, lb, n))
            m.addSeparator()
            off = m.addAction("No automatic EQ")
            off.setEnabled(bool(now))
            off.triggered.connect(lambda _c=False, k=kind, ky=key, lb=label: self.add_auto_rule(k, ky, lb, None))

    def save_eq(self) -> None:
        self.eq_store.save(self._out_id(), self.eq)

    def set_eq_per_device(self, on: bool) -> None:
        self.eq_store.per_device = on
        self._eq_device = self._out_id()
        self.save_eq()                      # current curve becomes this device's (or the shared) profile

    def _refresh_eq_button(self) -> None:
        eff = self._auto_eq[1] if getattr(self, "_auto_eq", None) else self.eq
        on = eff.enabled and self.engine.supports_eq
        self._set_toggle(self.btn_eq, "equalizer", on)
        eff = self._auto_eq[1] if self._auto_eq else self.eq
        what = f"Studio EQ ({eff.studio.name})" if eff.studio_on else eff.preset
        if self._auto_eq:
            what += f", automatic for {self._auto_eq[0]['label']}"
        self.btn_eq.setToolTip(f"Equalizer: {what if eff.enabled else 'off'} (Ctrl+E)")

    def restart_app(self) -> None:
        """Relaunch (used after installing libmpv). State is saved by closeEvent first."""
        args = sys.argv[1:] if BUNDLED else sys.argv
        if self.close():
            QProcess.startDetached(sys.executable, args)
            QApplication.quit()

    def show_equalizer(self, page: int = 0) -> None:
        if self._eq_dialog is None:
            self._eq_dialog = EqualizerDialog(self)
        self._eq_dialog.sync()
        self._eq_dialog.show_page(page)
        self._eq_dialog.show()
        self._eq_dialog.raise_()
        self._eq_dialog.activateWindow()
