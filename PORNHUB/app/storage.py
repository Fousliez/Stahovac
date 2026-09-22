from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class Storage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_file = self.data_dir / "jobs.json"
        self.settings_file = self.data_dir / "settings.json"
        self.archive_file = self.data_dir / "download_archive.txt"

        self._marker_lock = threading.RLock()
        self._init_marker_database()
        self._import_archive_file()
        self.sync_archive_from_database()

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
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def jobs(self) -> list[dict]:
        data = self._read_json(self.jobs_file, [])
        return data if isinstance(data, list) else []

    def save_jobs(self, jobs: list[dict]) -> None:
        self._write_json(self.jobs_file, jobs)

    def add_urls(self, urls: list[str]) -> int:
        jobs = self.jobs()
        known = {str(job.get("url", "")).strip() for job in jobs}
        added = 0
        for url in urls:
            value = url.strip()
            if not value or value in known:
                continue
            jobs.append({
                "url": value,
                "title": "",
                "status": "Připraveno",
                "progress": 0,
                "last_run": "",
            })
            known.add(value)
            added += 1
        self.save_jobs(jobs)
        return added

    def delete_urls(self, urls: list[str]) -> None:
        wanted = set(urls)
        self.save_jobs(
            [job for job in self.jobs() if str(job.get("url", "")) not in wanted]
        )

    def update_job(self, url: str, **changes) -> None:
        jobs = self.jobs()
        for job in jobs:
            if str(job.get("url", "")) == url:
                job.update(changes)
                break
        self.save_jobs(jobs)

    def get_setting(self, key: str, default: str = "") -> str:
        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            return default
        return str(data.get(key, default))

    def set_setting(self, key: str, value: str) -> None:
        old_rows = self._read_database_rows(self.marker_database()) if key == "marker_dir" else []

        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self._write_json(self.settings_file, data)

        if key == "marker_dir":
            new_db = self.marker_database()
            self._ensure_marker_schema(new_db)
            self._insert_download_rows(new_db, old_rows)

    def marker_database(self) -> Path:
        default_download_dir = str(Path.home() / "Stažené" / "Pornhub")
        download_dir = self.get_setting("download_dir", default_download_dir)
        marker_dir = Path(
            self.get_setting("marker_dir", download_dir)
        ).expanduser()
        return marker_dir / "PORNHUB_MARKERY.db"

    def mark_download(
        self,
        video_id: str,
        *,
        extractor: str = "",
        title: str = "",
        webpage_url: str = "",
        uploader: str = "",
        filepath: str = "",
        source_url: str = "",
    ) -> None:
        marker_id = str(video_id).strip()
        if not marker_id:
            return

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)

        with self._marker_lock:
            with sqlite3.connect(db_path, timeout=30) as connection:
                connection.execute(
                    """
                    INSERT INTO downloads(
                        id, extractor, title, webpage_url, uploader, filepath, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        extractor = CASE WHEN excluded.extractor <> '' THEN excluded.extractor ELSE downloads.extractor END,
                        title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE downloads.title END,
                        webpage_url = CASE WHEN excluded.webpage_url <> '' THEN excluded.webpage_url ELSE downloads.webpage_url END,
                        uploader = CASE WHEN excluded.uploader <> '' THEN excluded.uploader ELSE downloads.uploader END,
                        filepath = CASE WHEN excluded.filepath <> '' THEN excluded.filepath ELSE downloads.filepath END,
                        source_url = CASE WHEN excluded.source_url <> '' THEN excluded.source_url ELSE downloads.source_url END
                    """,
                    (
                        marker_id,
                        str(extractor).strip(),
                        str(title).strip(),
                        str(webpage_url).strip(),
                        str(uploader).strip(),
                        str(filepath).strip(),
                        str(source_url).strip(),
                    ),
                )
                connection.commit()

        self._ensure_archive_entry(str(extractor).strip(), marker_id)

    def is_downloaded(self, video_id: str) -> bool:
        marker_id = str(video_id).strip()
        if not marker_id:
            return False
        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            row = connection.execute(
                "SELECT 1 FROM downloads WHERE id = ? LIMIT 1",
                (marker_id,),
            ).fetchone()
        return row is not None

    def downloaded_count(self) -> int:
        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            row = connection.execute("SELECT COUNT(*) FROM downloads").fetchone()
        return int(row[0] if row else 0)

    def _init_marker_database(self) -> None:
        self._ensure_marker_schema(self.marker_database())

    @staticmethod
    def _ensure_marker_schema(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    extractor TEXT,
                    title TEXT,
                    webpage_url TEXT,
                    uploader TEXT,
                    filepath TEXT,
                    source_url TEXT,
                    downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_downloads_uploader
                    ON downloads(uploader);
                CREATE INDEX IF NOT EXISTS idx_downloads_source_url
                    ON downloads(source_url);
                CREATE INDEX IF NOT EXISTS idx_downloads_downloaded_at
                    ON downloads(downloaded_at);
                """
            )
            connection.commit()

    def _read_database_rows(self, path: Path) -> list[tuple]:
        if not path.exists():
            return []
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                return connection.execute(
                    """
                    SELECT id, extractor, title, webpage_url, uploader,
                           filepath, source_url, downloaded_at
                    FROM downloads
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

    def _insert_download_rows(self, path: Path, rows: list[tuple]) -> None:
        if not rows:
            return
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO downloads(
                    id, extractor, title, webpage_url, uploader,
                    filepath, source_url, downloaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()

    def _import_archive_file(self) -> None:
        try:
            lines = self.archive_file.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            return

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        rows: list[tuple[str, str]] = []
        for line in lines:
            value = line.strip()
            if not value:
                continue
            parts = value.split(maxsplit=1)
            if len(parts) == 2:
                extractor, video_id = parts
            else:
                extractor, video_id = "", parts[0]
            if video_id:
                rows.append((video_id, extractor))

        if not rows:
            return

        with sqlite3.connect(db_path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO downloads(id, extractor)
                VALUES (?, ?)
                """,
                rows,
            )
            connection.commit()

    def sync_archive_from_database(self) -> None:
        rows = self._read_database_rows(self.marker_database())
        for video_id, extractor, *_rest in rows:
            self._ensure_archive_entry(str(extractor or ""), str(video_id or ""))

    def _ensure_archive_entry(self, extractor: str, video_id: str) -> None:
        video_id = video_id.strip()
        if not video_id:
            return
        extractor = extractor.strip()
        line = f"{extractor} {video_id}".strip()

        try:
            existing = {
                value.strip()
                for value in self.archive_file.read_text(encoding="utf-8").splitlines()
                if value.strip()
            }
        except (FileNotFoundError, OSError):
            existing = set()

        if line in existing:
            return

        self.archive_file.parent.mkdir(parents=True, exist_ok=True)
        with self.archive_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
