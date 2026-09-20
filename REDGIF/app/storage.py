from __future__ import annotations

import json
import re
import sqlite3
import threading
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

        self._marker_lock = threading.RLock()
        self._marker_ids: set[str] = set()
        self._init_marker_database()

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

    @staticmethod
    def normalize_marker_id(gif_id: str) -> str:
        return gif_id.strip().casefold()

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
                "name": "",
                "username": username,
                "url": url,
                "last_scan": "",
                "total": 0,
                "new": 0,
            }
        )
        profiles.sort(key=lambda p: str(p.get("username", "")).casefold())
        self._save_profiles(profiles)

    def set_profile_name(self, username: str, name: str) -> None:
        profiles = self.profiles()
        needle = username.casefold()
        for profile in profiles:
            if str(profile.get("username", "")).casefold() == needle:
                profile["name"] = name.strip()
                break
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
        # Záznamy v databázi záměrně nemažeme. Při opětovném přidání profilu
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
        existing_ids = set(self._marker_ids) if key == "download_dir" else None

        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self._write_json(self.settings_file, data)

        if key == "download_dir":
            db_path = self.marker_database()
            database_was_new = not db_path.exists()
            self._ensure_marker_schema(db_path)

            ids = self._read_database_ids(db_path)
            if database_was_new:
                ids |= self._read_legacy_ids(db_path.parent)

            ids |= existing_ids or set()
            self._insert_marker_ids(db_path, ids)

            with self._marker_lock:
                self._marker_ids = ids

    def scan_path(self, username: str) -> Path:
        return self.scans_dir / f"{self.safe_name(username)}.json"

    def save_scan(self, username: str, items: list[dict]) -> None:
        self._write_json(self.scan_path(username), items)

    def load_scan(self, username: str) -> list[dict]:
        data = self._read_json(self.scan_path(username), [])
        return data if isinstance(data, list) else []

    def marker_database(self) -> Path:
        default_dir = str(Path.home() / "Stažené" / "RedGIF")
        download_dir = Path(
            self.get_setting("download_dir", default_dir)
        ).expanduser()
        return download_dir / "REDGIF_MARKERY.db"

    def is_marked(self, username: str, gif_id: str) -> bool:
        del username
        marker_id = self.normalize_marker_id(gif_id)
        if not marker_id:
            return False
        with self._marker_lock:
            return marker_id in self._marker_ids

    def mark(self, username: str, gif_id: str) -> None:
        marker_id = self.normalize_marker_id(gif_id)
        if not marker_id:
            return

        with self._marker_lock:
            if marker_id in self._marker_ids:
                return

            db_path = self.marker_database()
            self._ensure_marker_schema(db_path)
            with sqlite3.connect(db_path, timeout=30) as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO downloads (id, profile)
                    VALUES (?, ?)
                    """,
                    (marker_id, username),
                )
                connection.commit()

            self._marker_ids.add(marker_id)

    def downloaded_count(self, username: str, items: list[dict] | None = None) -> int:
        if items is None:
            items = self.load_scan(username)

        with self._marker_lock:
            marker_ids = self._marker_ids
            return sum(
                1
                for item in items
                if self.normalize_marker_id(str(item.get("id", ""))) in marker_ids
            )

    def new_items(self, username: str, items: list[dict] | None = None) -> list[dict]:
        if items is None:
            items = self.load_scan(username)

        with self._marker_lock:
            marker_ids = self._marker_ids
            return [
                item
                for item in items
                if self.normalize_marker_id(str(item.get("id", ""))) not in marker_ids
            ]

    def _init_marker_database(self) -> None:
        db_path = self.marker_database()
        database_was_new = not db_path.exists()
        self._ensure_marker_schema(db_path)

        ids = self._read_database_ids(db_path)
        if database_was_new:
            ids |= self._read_legacy_ids(db_path.parent)
            self._insert_marker_ids(db_path, ids)

        with self._marker_lock:
            self._marker_ids = ids

    @staticmethod
    def _ensure_marker_schema(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    profile TEXT,
                    downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_profile ON downloads(profile)"
            )
            connection.commit()

    def _read_database_ids(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                rows = connection.execute("SELECT id FROM downloads").fetchall()
        except sqlite3.Error:
            return set()

        return {
            marker_id
            for (raw_id,) in rows
            if (marker_id := self.normalize_marker_id(str(raw_id)))
        }

    def _insert_marker_ids(self, path: Path, ids: set[str]) -> None:
        if not ids:
            return

        self._ensure_marker_schema(path)
        rows = [(marker_id,) for marker_id in ids]
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO downloads (id) VALUES (?)",
                rows,
            )
            connection.commit()

    def _read_legacy_ids(self, download_dir: Path) -> set[str]:
        ids = self._read_marker_text_file(download_dir / "REDGIF_MARKERY.txt")
        ids |= self._read_done_markers(download_dir / "REDGIF_MARKERY")
        ids |= self._read_done_markers(self.legacy_markers_dir)
        return ids

    def _read_marker_text_file(self, path: Path) -> set[str]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            return set()

        return {
            marker_id
            for line in lines
            if (marker_id := self.normalize_marker_id(line))
        }

    def _read_done_markers(self, directory: Path) -> set[str]:
        if not directory.is_dir():
            return set()

        return {
            marker_id
            for path in directory.rglob("*.done")
            if (marker_id := self.normalize_marker_id(path.stem))
        }
