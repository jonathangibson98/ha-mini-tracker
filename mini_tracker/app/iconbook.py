"""Everything icon-related: MDI (bundled), your custom SVG folders, and search.

Spec strings look like Home Assistant's: "mdi:home", "hass:home", "local:garage",
"custom:garage", "<prefix>:<name>" for folders registered with Auto Custom Icons.
"""
from __future__ import annotations

import json
import os
import re
import threading

MDI_ALIASES = ("mdi", "hass")
FOLDER_ALIASES = ("local", "custom")          # both mean <config>/<icons_folder>
RESERVED = set(MDI_ALIASES) | set(FOLDER_ALIASES)
_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")
_NAME_OK = re.compile(r"^[A-Za-z0-9_\-./ ]{1,120}$")
MDI_FILE = os.path.join(os.path.dirname(__file__), "mdi.json")


class IconBook:
    def __init__(self, config_dir: str, icons_folder) -> None:
        self.config_dir = config_dir
        self._icons_folder = icons_folder          # callable -> folder name (from settings)
        self._lock = threading.Lock()
        self._mdi: dict | None = None
        self._ce_mtime = -1.0
        self._ce_sets: dict[str, str] = {}

    # --------------------------------------------------------------- MDI
    def _load_mdi(self) -> dict:
        with self._lock:
            if self._mdi is None:
                try:
                    with open(MDI_FILE, encoding="utf-8") as fp:
                        self._mdi = json.load(fp)
                except (OSError, ValueError):
                    self._mdi = {"version": "?", "icons": {}, "aliases": {}}
            return self._mdi

    def mdi_version(self) -> str:
        return self._load_mdi().get("version", "?")

    def mdi_count(self) -> int:
        return len(self._load_mdi()["icons"])

    def mdi_path(self, name: str) -> str | None:
        m = self._load_mdi()
        name = name.strip().lower()
        d = m["icons"].get(name)
        if d is None:
            real = m["aliases"].get(name)
            d = m["icons"].get(real) if real else None
        return d

    @staticmethod
    def mdi_svg(path: str, color: str | None) -> bytes:
        c = color if (color and _COLOR.match(color)) else "000000"
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                f'<path fill="#{c}" d="{path}"/></svg>').encode()

    # --------------------------------------------------------------- folder sets
    def _ce_file(self) -> str:
        return os.path.join(self.config_dir, ".storage", "core.config_entries")

    def _auto_custom_icons_sets(self) -> dict[str, str]:
        """Folders added through the Auto Custom Icons integration (read-only peek)."""
        p = self._ce_file()
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return {}
        if mt == self._ce_mtime:
            return self._ce_sets
        sets: dict[str, str] = {}
        try:
            with open(p, encoding="utf-8") as fp:
                entries = (json.load(fp).get("data") or {}).get("entries") or []
            for e in entries:
                if e.get("domain") != "auto_custom_icons":
                    continue
                d = e.get("data") or {}
                folder, prefix = str(d.get("folder", "")), str(d.get("prefix", "")).lower()
                if folder and prefix and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", folder) \
                        and re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", prefix) and prefix not in RESERVED:
                    sets[prefix] = os.path.join(self.config_dir, folder)
        except (OSError, ValueError, AttributeError):
            sets = {}
        self._ce_mtime, self._ce_sets = mt, sets
        return sets

    def folder_sets(self) -> dict[str, dict]:
        """prefix -> {dir, source}. Aliases local/custom share the configured icons folder."""
        out: dict[str, dict] = {}
        root = os.path.join(self.config_dir, self._icons_folder())
        for a in FOLDER_ALIASES:
            out[a] = {"dir": root, "source": "icons folder"}
        for prefix, d in self._auto_custom_icons_sets().items():
            out[prefix] = {"dir": d, "source": "Auto Custom Icons"}
        return out

    @staticmethod
    def _list_dir(root: str) -> list[str]:
        names = []
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".")]
            for f in fn:
                if f.endswith(".svg") and not f.startswith("."):
                    rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
                    names.append(rel[:-4])
        return sorted(names)

    def folder_icons(self, prefix: str) -> list[str]:
        s = self.folder_sets().get(prefix)
        return self._list_dir(s["dir"]) if s else []

    def folder_path(self, prefix: str, name: str) -> str | None:
        s = self.folder_sets().get(prefix)
        if not s or not _NAME_OK.match(name) or ".." in name.split("/"):
            return None
        root = os.path.realpath(s["dir"])
        full = os.path.realpath(os.path.join(root, name + ".svg"))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        return full

    # --------------------------------------------------------------- resolve
    def resolve(self, prefix: str, name: str, color: str | None):
        """Returns ("svg", bytes) | ("file", path) | None."""
        prefix = prefix.lower()
        if prefix in MDI_ALIASES:
            d = self.mdi_path(name)
            return ("svg", self.mdi_svg(d, color)) if d else None
        p = self.folder_path(prefix, name)
        return ("file", p) if p else None

    # --------------------------------------------------------------- search / listing
    def sets_summary(self) -> list[dict]:
        out = [{"prefix": "mdi", "count": self.mdi_count(), "source": f"Material Design Icons {self.mdi_version()}"}]
        seen_roots: dict[str, str] = {}
        for prefix, s in self.folder_sets().items():
            if s["dir"] in seen_roots and prefix in FOLDER_ALIASES:
                continue            # local + custom are the same folder: list once
            seen_roots[s["dir"]] = prefix
            out.append({"prefix": prefix, "count": len(self._list_dir(s["dir"])), "source": s["source"],
                        "alias": "custom" if prefix == "local" else None})
        return out

    def search(self, q: str, set_name: str = "", limit: int = 80) -> list[str]:
        q = q.strip().lower()
        limit = max(1, min(limit, 300))
        results: list[str] = []

        def rank(names: list[str], prefix: str) -> list[str]:
            if not q:
                return [f"{prefix}:{n}" for n in names]
            starts = [n for n in names if n.lower().startswith(q)]
            contains = [n for n in names if q in n.lower() and not n.lower().startswith(q)]
            return [f"{prefix}:{n}" for n in starts + contains]

        want = set_name.lower()
        if want in ("", "all"):
            for prefix, s in self.folder_sets().items():
                if prefix == "custom":
                    continue                      # same folder as "local"
                results += rank(self._list_dir(s["dir"]), prefix)
            results += rank(sorted(self._load_mdi()["icons"]), "mdi")
        elif want in MDI_ALIASES:
            results = rank(sorted(self._load_mdi()["icons"]), "mdi")
        else:
            results = rank(self.folder_icons(want), want)
        return results[:limit]
