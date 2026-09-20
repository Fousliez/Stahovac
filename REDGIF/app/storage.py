from __future__ import annotations

import json
import re
from pathlib import Path


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class Storage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_file = self.data_dir / "profiles.json"
        self.settings_file = self.data_dir / "settings.json"
        self.scans_dir = self.data_dir / "scans"
        self.legacy_markers_dir = self.data_dir / "markers"
        self.scans_dir.mkdir(parents=True, exist_ok=True)
        self.migrate_legacy_markers()

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)

    @staticmethod
    def safe_name(value: str) -> str:
        cleaned = SAFE_NAME_RE.sub("_", value.strip())
        return cleaned or "_"

    def profiles(self) -> list[dict]:
        data = self._read_json(self.profiles_file, [])
        return data if isinstance(data, list) else []

    def _save_profiles(self, profiles: list[dict]) -> None:
        self._write_json(self.profiles_file, profiles)

    def profile(self, username: str) -> dict | None:
        needle = username.casefold()
        for profile in self.profiles():
            if str(profile.get("username", "")).casefold() == needle:
                return profile
        return None

    def add_profile(self, username: str, url: str) -> None:
        profiles = self.profiles()
        needle = username.casefold()
        if any(str(p.get("username", "")).casefold() == needle for p in profiles):
            return
        profiles.append(
            {
                "username": username,
                "url": url,
                "last_scan": "",
                "total": 0,
                "new": 0,
            }
        )
        profiles.sort(key=lambda p: str(p.get("username", "")).casefold())
        self._save_profiles(profiles)

    def delete_profile(self, username: str) -> None:
        needle = username.casefold()
        profiles = [
            p for p in self.profiles()
            if str(p.get("username", "")).casefold() != needle
        ]
        self._save_profiles(profiles)
        scan_path = self.scan_path(username)
        try:
            scan_path.unlink()
        except FileNotFoundError:
            pass
        # Společné markery záměrně nemažeme. Při opětovném přidání profilu
        # tak program stále ví, co už bylo v minulosti staženo.

    def update_scan_stats(self, username: str, last_scan: str, total: int, new: int) -> None:
        profiles = self.profiles()
        needle = username.casefold()
        for profile in profiles:
            if str(profile.get("username", "")).casefold() == needle:
                profile["last_scan"] = last_scan
                profile["total"] = int(total)
                profile["new"] = int(new)
                break
        self._save_profiles(profiles)

    def get_setting(self, key: str, default: str = "") -> str:
        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            return default
        return str(data.get(key, default))

    def set_setting(self, key: str, value: str) -> None:
        old_marker_dir = self.marker_dir() if key == "download_dir" else None

        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self._write_json(self.settings_file, data)

        if key == "download_dir":
            new_marker_dir = self.marker_dir()
            if old_marker_dir is not None and old_marker_dir != new_marker_dir:
                self._copy_markers(old_marker_dir, new_marker_dir)
            self.migrate_legacy_markers()

    def scan_path(self, username: str) -> Path:
        return self.scans_dir / f"{self.safe_name(username)}.json"

    def save_scan(self, username: str, items: list[dict]) -> None:
        self._write_json(self.scan_path(username), items)

    def load_scan(self, username: str) -> list[dict]:
        data = self._read_json(self.scan_path(username), [])
        return data if isinstance(data, list) else []

    def marker_dir(self) -> Path:
        default_dir = str(Path.home() / "Stažené" / "RedGIF")
        download_dir = Path(
            self.get_setting("download_dir", default_dir)
        ).expanduser()
        return download_dir / "REDGIF_MARKERY"

    def marker_path(self, username: str, gif_id: str) -> Path:
        # RedGIF ID je globálně unikátní, proto markery nemusíme dělit
        # podle profilů.
        return self.marker_dir() / f"{self.safe_name(gif_id)}.done"

    def is_marked(self, username: str, gif_id: str) -> bool:
        return self.marker_path(username, gif_id).exists()

    def mark(self, username: str, gif_id: str) -> None:
        path = self.marker_path(username, gif_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def downloaded_count(self, username: str, items: list[dict] | None = None) -> int:
        if items is None:
            items = self.load_scan(username)
        return sum(
            1
            for item in items
            if self.is_marked(username, str(item.get("id", "")))
        )

    def new_items(self, username: str, items: list[dict] | None = None) -> list[dict]:
        if items is None:
            items = self.load_scan(username)
        return [
            item for item in items
            if not self.is_marked(username, str(item.get("id", "")))
        ]

    @staticmethod
    def _copy_markers(source: Path, destination: Path) -> int:
        if not source.is_dir():
            return 0

        destination.mkdir(parents=True, exist_ok=True)
        copied = 0
        for marker in source.rglob("*.done"):
            target = destination / marker.name
            if not target.exists():
                target.touch()
                copied += 1
        return copied

    def migrate_legacy_markers(self) -> int:
        # Starší verze ukládala markery do data/markers/<profil>/.
        return self._copy_markers(self.legacy_markers_dir, self.marker_dir())
