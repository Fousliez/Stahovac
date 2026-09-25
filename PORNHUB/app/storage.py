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
        self.categories_file = self.data_dir / "categories.json"
        self.settings_file = self.data_dir / "settings.json"
        self.archive_file = self.data_dir / "download_archive.txt"

        if not self.categories_file.exists():
            self._write_json(
                self.categories_file,
                ["Ruined", "Femdom", "Latex"],
            )

        self._marker_lock = threading.RLock()
        self.remove_setting("quality")
        self.remove_setting("reference_url")
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

    def categories(self) -> list[str]:
        raw = self._read_json(self.categories_file, [])
        values: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                name = str(value or "").strip()
                if name and name.casefold() not in {item.casefold() for item in values}:
                    values.append(name)

        # Kategorie z existujících záznamů zachováme i po případné ruční
        # úpravě categories.json.
        for job in self.jobs():
            name = str(job.get("category") or "").strip()
            if name and name.casefold() not in {item.casefold() for item in values}:
                values.append(name)

        return values

    def add_category(self, name: str) -> str:
        value = str(name or "").strip()
        if not value:
            return ""

        categories = self.categories()
        for existing in categories:
            if existing.casefold() == value.casefold():
                return existing

        categories.append(value)
        self._write_json(self.categories_file, categories)
        return value

    def set_category(self, urls: list[str], category: str) -> None:
        wanted = {str(url or "").strip() for url in urls if str(url or "").strip()}
        if not wanted:
            return

        value = str(category or "").strip()
        if value:
            value = self.add_category(value)

        jobs = self.jobs()
        changed = False
        for job in jobs:
            if str(job.get("url", "")).strip() in wanted:
                if str(job.get("category") or "").strip() != value:
                    job["category"] = value
                    changed = True

        if changed:
            self.save_jobs(jobs)

    def add_source_entries(self, entries: list[dict]) -> tuple[int, int]:
        """Přidá profily a uloží k nim volitelné poslední známé video."""
        jobs = self.jobs()
        by_url = {
            str(job.get("url", "")).strip(): job
            for job in jobs
            if str(job.get("url", "")).strip()
        }
        added = 0
        references_updated = 0

        for entry in entries:
            value = str(entry.get("url") or "").strip()
            reference_url = str(entry.get("reference_url") or "").strip()
            if not value:
                continue

            existing = by_url.get(value)
            if existing is not None:
                if (
                    reference_url
                    and str(existing.get("ph_reference_url") or "").strip()
                    != reference_url
                ):
                    existing["ph_reference_url"] = reference_url
                    references_updated += 1
                continue

            job = {
                "url": value,
                "title": "",
                "status": "Připraveno",
                "progress": 0,
                "last_run": "",
                "last_error": "",
                "ph_user_id": "",
                "ph_profile_name": "",
                "ph_profile_path": "",
                "ph_reference_url": reference_url,
                "recovery_videos": [],
                "previous_urls": [],
                "category": "",
                "info_checked": False,
            }
            jobs.append(job)
            by_url[value] = job
            added += 1
            if reference_url:
                references_updated += 1

        self.save_jobs(jobs)
        return added, references_updated

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
                "last_error": "",
                "ph_user_id": "",
                "ph_profile_name": "",
                "ph_profile_path": "",
                "recovery_videos": [],
                "previous_urls": [],
                "category": "",
                "info_checked": False,
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

        if not wanted:
            return
        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        placeholders = ",".join("?" for _ in wanted)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            connection.execute(
                f"DELETE FROM source_items WHERE source_url IN ({placeholders})",
                list(wanted),
            )
            connection.commit()

    def update_job(self, url: str, **changes) -> None:
        jobs = self.jobs()
        for job in jobs:
            if str(job.get("url", "")) == url:
                job.update(changes)
                break
        self.save_jobs(jobs)

    def job(self, url: str) -> dict | None:
        wanted = str(url or "").strip()
        for job in self.jobs():
            if str(job.get("url", "")).strip() == wanted:
                return job
        return None

    def replace_source_url(self, old_url: str, new_url: str) -> None:
        """Přejmenuje zdroj bez ztráty scanů, baseline ani historie stahování."""
        old_value = str(old_url or "").strip()
        new_value = str(new_url or "").strip()
        if not old_value or not new_value or old_value == new_value:
            return

        jobs = self.jobs()
        old_job = next(
            (job for job in jobs if str(job.get("url", "")).strip() == old_value),
            None,
        )
        if old_job is None:
            raise ValueError("Původní profil už není v seznamu.")

        if any(
            job is not old_job and str(job.get("url", "")).strip() == new_value
            for job in jobs
        ):
            raise ValueError(
                "Nová adresa profilu už v seznamu existuje. "
                "Automatická změna proto nebyla provedena."
            )

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO known_items(
                    source_url, id, extractor, title, webpage_url, known_at
                )
                SELECT ?, id, extractor, title, webpage_url, known_at
                FROM known_items
                WHERE source_url = ?
                """,
                (new_value, old_value),
            )
            connection.execute(
                "DELETE FROM known_items WHERE source_url = ?",
                (old_value,),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO source_items(
                    source_url, id, extractor, title, webpage_url, seen_at
                )
                SELECT ?, id, extractor, title, webpage_url, seen_at
                FROM source_items
                WHERE source_url = ?
                """,
                (new_value, old_value),
            )
            connection.execute(
                "DELETE FROM source_items WHERE source_url = ?",
                (old_value,),
            )
            connection.execute(
                "UPDATE downloads SET source_url = ? WHERE source_url = ?",
                (new_value, old_value),
            )
            connection.commit()

        previous_urls = old_job.get("previous_urls") or []
        if not isinstance(previous_urls, list):
            previous_urls = []
        previous_urls = [
            str(value).strip()
            for value in previous_urls
            if str(value).strip()
        ]
        if old_value not in previous_urls:
            previous_urls.append(old_value)

        old_job["url"] = new_value
        old_job["previous_urls"] = previous_urls[-20:]
        self.save_jobs(jobs)

    def get_setting(self, key: str, default: str = "") -> str:
        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            return default
        return str(data.get(key, default))

    def set_setting(self, key: str, value: str) -> None:
        old_rows = self._read_database_rows(self.marker_database()) if key == "marker_dir" else []
        old_known_rows = self._read_known_rows(self.marker_database()) if key == "marker_dir" else []
        old_scan_rows = self._read_scan_rows(self.marker_database()) if key == "marker_dir" else []

        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self._write_json(self.settings_file, data)

        if key == "marker_dir":
            new_db = self.marker_database()
            self._ensure_marker_schema(new_db)
            self._insert_download_rows(new_db, old_rows)
            self._insert_known_rows(new_db, old_known_rows)
            self._insert_scan_rows(new_db, old_scan_rows)

    def remove_setting(self, key: str) -> None:
        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict) or key not in data:
            return
        data.pop(key, None)
        self._write_json(self.settings_file, data)

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
                connection.execute(
                    "DELETE FROM known_items WHERE id = ?",
                    (marker_id,),
                )
                connection.commit()

        self._ensure_archive_entry(str(extractor).strip(), marker_id)

    def save_scan(self, source_url: str, items: list[dict]) -> None:
        source = str(source_url or "").strip()
        if not source:
            return

        rows: list[tuple[str, str, str, str, str]] = []
        for item in items:
            video_id = str(item.get("id", "")).strip()
            if not video_id:
                continue
            rows.append(
                (
                    source,
                    video_id,
                    str(item.get("extractor", "") or "pornhub").strip().casefold(),
                    str(item.get("title", "")).strip(),
                    str(item.get("webpage_url", "")).strip(),
                )
            )

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            connection.execute(
                "DELETE FROM source_items WHERE source_url = ?",
                (source,),
            )
            if rows:
                connection.executemany(
                    """
                    INSERT INTO source_items(
                        source_url, id, extractor, title, webpage_url
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            connection.commit()

    def source_items(self, source_url: str) -> list[dict]:
        source = str(source_url or "").strip()
        if not source:
            return []

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            rows = connection.execute(
                """
                SELECT id, extractor, title, webpage_url
                FROM source_items
                WHERE source_url = ?
                ORDER BY rowid
                """,
                (source,),
            ).fetchall()

        return [
            {
                "id": str(row[0] or ""),
                "extractor": str(row[1] or ""),
                "title": str(row[2] or ""),
                "webpage_url": str(row[3] or ""),
            }
            for row in rows
        ]

    def scan_counts(self, source_url: str) -> tuple[int, int, int]:
        source = str(source_url or "").strip()
        if not source:
            return 0, 0, 0

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    SUM(
                        CASE
                            WHEN d.id IS NOT NULL OR k.id IS NOT NULL THEN 1
                            ELSE 0
                        END
                    ) AS downloaded_count,
                    SUM(
                        CASE
                            WHEN d.id IS NULL AND k.id IS NULL THEN 1
                            ELSE 0
                        END
                    ) AS new_count
                FROM source_items s
                LEFT JOIN downloads d
                    ON d.id = s.id
                LEFT JOIN known_items k
                    ON k.source_url = s.source_url AND k.id = s.id
                WHERE s.source_url = ?
                """,
                (source,),
            ).fetchone()

        if row is None:
            return 0, 0, 0
        return (
            int(row[0] or 0),
            int(row[1] or 0),
            int(row[2] or 0),
        )

    def new_items(self, source_url: str) -> list[dict]:
        source = str(source_url or "").strip()
        if not source:
            return []

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.extractor, s.title, s.webpage_url
                FROM source_items s
                LEFT JOIN downloads d
                    ON d.id = s.id
                LEFT JOIN known_items k
                    ON k.source_url = s.source_url AND k.id = s.id
                WHERE s.source_url = ?
                  AND d.id IS NULL
                  AND k.id IS NULL
                ORDER BY s.rowid
                """,
                (source,),
            ).fetchall()

        return [
            {
                "id": str(row[0] or ""),
                "extractor": str(row[1] or ""),
                "title": str(row[2] or ""),
                "webpage_url": str(row[3] or ""),
            }
            for row in rows
        ]

    def mark_known_items(self, source_url: str, items: list[dict]) -> int:
        source = str(source_url or "").strip()
        rows: list[tuple[str, str, str, str, str]] = []
        for item in items:
            video_id = str(item.get("id", "")).strip()
            if not video_id:
                continue
            extractor = "pornhub"
            rows.append(
                (
                    source,
                    video_id,
                    extractor,
                    str(item.get("title", "")).strip(),
                    str(item.get("webpage_url", "")).strip(),
                )
            )

        if not rows:
            return 0

        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO known_items(
                    source_url, id, extractor, title, webpage_url
                )
                SELECT ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM downloads WHERE id = ?
                )
                """,
                [(*row, row[1]) for row in rows],
            )
            added = connection.total_changes - before
            connection.commit()

        for _source, video_id, extractor, _title, _webpage_url in rows:
            self._ensure_archive_entry(extractor, video_id)
        return int(added)

    def known_count(self, source_url: str) -> int:
        source = str(source_url or "").strip()
        db_path = self.marker_database()
        self._ensure_marker_schema(db_path)
        with self._marker_lock, sqlite3.connect(db_path, timeout=30) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM known_items WHERE source_url = ?",
                (source,),
            ).fetchone()
        return int(row[0] if row else 0)

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

                CREATE TABLE IF NOT EXISTS known_items (
                    source_url TEXT NOT NULL,
                    id TEXT NOT NULL,
                    extractor TEXT,
                    title TEXT,
                    webpage_url TEXT,
                    known_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(source_url, id)
                );

                CREATE TABLE IF NOT EXISTS source_items (
                    source_url TEXT NOT NULL,
                    id TEXT NOT NULL,
                    extractor TEXT,
                    title TEXT,
                    webpage_url TEXT,
                    seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(source_url, id)
                );

                CREATE INDEX IF NOT EXISTS idx_source_items_source_url
                    ON source_items(source_url);
                CREATE INDEX IF NOT EXISTS idx_source_items_id
                    ON source_items(id);
                CREATE INDEX IF NOT EXISTS idx_known_items_source_url
                    ON known_items(source_url);
                CREATE INDEX IF NOT EXISTS idx_known_items_id
                    ON known_items(id);
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

    @staticmethod
    def _read_scan_rows(path: Path) -> list[tuple]:
        if not path.exists():
            return []
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                return connection.execute(
                    """
                    SELECT source_url, id, extractor, title, webpage_url, seen_at
                    FROM source_items
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

    def _insert_scan_rows(self, path: Path, rows: list[tuple]) -> None:
        if not rows:
            return
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO source_items(
                    source_url, id, extractor, title, webpage_url, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()

    @staticmethod
    def _read_known_rows(path: Path) -> list[tuple]:
        if not path.exists():
            return []
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                return connection.execute(
                    """
                    SELECT source_url, id, extractor, title, webpage_url, known_at
                    FROM known_items
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

    def _insert_known_rows(self, path: Path, rows: list[tuple]) -> None:
        if not rows:
            return
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO known_items(
                    source_url, id, extractor, title, webpage_url, known_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()

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
        known_ids = {
            str(row[1]).strip()
            for row in self._read_known_rows(db_path)
            if len(row) > 1 and str(row[1]).strip()
        }
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
            if video_id and video_id not in known_ids:
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
        db_path = self.marker_database()
        rows = self._read_database_rows(db_path)
        for video_id, extractor, *_rest in rows:
            self._ensure_archive_entry(str(extractor or ""), str(video_id or ""))

        for _source_url, video_id, _extractor, *_rest in self._read_known_rows(db_path):
            self._ensure_archive_entry("pornhub", str(video_id or ""))

    def _ensure_archive_entry(self, extractor: str, video_id: str) -> None:
        video_id = video_id.strip()
        if not video_id:
            return
        extractor = extractor.strip().casefold()
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
