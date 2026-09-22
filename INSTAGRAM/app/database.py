from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


VALID_POST_STATUSES = {"new", "known", "downloaded", "ignored", "error"}


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self._init_marker_database()
        self._migrate_downloaded_posts_to_markers()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_scan_at TEXT,
                    first_scan_done INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    shortcode TEXT NOT NULL,
                    post_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new'
                        CHECK(status IN ('new', 'known', 'downloaded', 'ignored', 'error')),
                    discovered_at TEXT NOT NULL,
                    processed_at TEXT,
                    UNIQUE(profile_id, shortcode),
                    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_posts_profile_status
                    ON posts(profile_id, status);
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_profile(self, username: str, url: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO profiles(username, url, created_at) VALUES(?, ?, ?)",
                (username.strip(), url.strip(), self.now()),
            )
            return int(cur.lastrowid)

    def delete_profile(self, profile_id: int) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def profiles(self):
        with self.connect() as con:
            return con.execute(
                """
                SELECT p.*,
                       COUNT(po.id) AS total_posts,
                       SUM(CASE WHEN po.status = 'new' THEN 1 ELSE 0 END) AS new_posts
                FROM profiles p
                LEFT JOIN posts po ON po.profile_id = p.id
                GROUP BY p.id
                ORDER BY p.username COLLATE NOCASE
                """
            ).fetchall()

    def profile(self, profile_id: int):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()

    def register_scan(
        self,
        profile_id: int,
        posts: list[dict],
        first_scan_as_new: bool = False,
    ) -> tuple[int, int]:
        """Zapíše výsledek průchodu.

        Při úplně prvním průchodu se aktuální obsah uloží jako `known`, aby
        program vytvořil výchozí bod. Při dalších průchodech dostanou nově
        nalezené posty stav `new`. Už známé ID se nikdy nepřepisuje.
        """
        new_count = 0
        with self.connect() as con:
            profile = con.execute(
                "SELECT first_scan_done FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                return 0, 0

            first_scan = not bool(profile["first_scan_done"])
            initial_status = (
                "new"
                if first_scan_as_new
                else ("known" if first_scan else "new")
            )
            now = self.now()

            for post in posts:
                before = con.total_changes
                con.execute(
                    """
                    INSERT OR IGNORE INTO posts(
                        profile_id, shortcode, post_url, status, discovered_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        post["shortcode"],
                        post["post_url"],
                        initial_status,
                        now,
                    ),
                )
                if con.total_changes > before and initial_status == "new":
                    new_count += 1

            con.execute(
                "UPDATE profiles SET last_scan_at = ?, first_scan_done = 1 WHERE id = ?",
                (now, profile_id),
            )

        return len(posts), new_count

    def posts_for_profile(self, profile_id: int, status: str | None = None):
        query = "SELECT * FROM posts WHERE profile_id = ?"
        params: list[object] = [profile_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY id DESC"
        with self.connect() as con:
            return con.execute(query, params).fetchall()

    def post(self, post_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    def set_post_status(self, post_id: int, status: str) -> None:
        self.set_posts_status([post_id], status)

    def set_posts_status(self, post_ids: list[int], status: str) -> None:
        if status not in VALID_POST_STATUSES:
            raise ValueError(f"Neplatný stav postu: {status}")
        ids = [int(value) for value in post_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as con:
            con.execute(
                f"UPDATE posts SET status = ?, processed_at = ? WHERE id IN ({placeholders})",
                [status, self.now(), *ids],
            )

    def mark_all_known(self, profile_id: int) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE posts
                SET status = 'known', processed_at = ?
                WHERE profile_id = ? AND status = 'new'
                """,
                (self.now(), profile_id),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        old_marker_rows = (
            self._read_marker_rows(self.marker_database())
            if key == "marker_dir"
            else []
        )

        with self.connect() as con:
            con.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

        if key == "marker_dir":
            new_path = self.marker_database()
            self._ensure_marker_schema(new_path)
            self._insert_marker_rows(new_path, old_marker_rows)
            self._migrate_downloaded_posts_to_markers()

    def marker_database(self) -> Path:
        default_dir = str(Path.home() / "Stažené" / "Instagram")
        download_dir = self.get_setting("download_dir", default_dir)
        marker_dir = Path(
            self.get_setting("marker_dir", download_dir)
        ).expanduser()
        return marker_dir / "INSTAGRAM_MARKERY.db"

    def mark_download(
        self,
        shortcode: str,
        *,
        username: str = "",
        post_url: str = "",
        destination: str = "",
    ) -> None:
        marker_id = str(shortcode).strip()
        if not marker_id:
            return

        path = self.marker_database()
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.execute(
                """
                INSERT INTO downloads(shortcode, username, post_url, destination)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(shortcode) DO UPDATE SET
                    username = CASE
                        WHEN excluded.username <> '' THEN excluded.username
                        ELSE downloads.username
                    END,
                    post_url = CASE
                        WHEN excluded.post_url <> '' THEN excluded.post_url
                        ELSE downloads.post_url
                    END,
                    destination = CASE
                        WHEN excluded.destination <> '' THEN excluded.destination
                        ELSE downloads.destination
                    END
                """,
                (
                    marker_id,
                    str(username).strip(),
                    str(post_url).strip(),
                    str(destination).strip(),
                ),
            )
            connection.commit()

    def is_downloaded_marker(self, shortcode: str) -> bool:
        marker_id = str(shortcode).strip()
        if not marker_id:
            return False
        path = self.marker_database()
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            row = connection.execute(
                "SELECT 1 FROM downloads WHERE shortcode = ? LIMIT 1",
                (marker_id,),
            ).fetchone()
        return row is not None

    def marker_count(self) -> int:
        path = self.marker_database()
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
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
                    shortcode TEXT PRIMARY KEY,
                    username TEXT,
                    post_url TEXT,
                    destination TEXT,
                    downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_instagram_downloads_username
                    ON downloads(username);
                CREATE INDEX IF NOT EXISTS idx_instagram_downloads_downloaded_at
                    ON downloads(downloaded_at);
                """
            )
            connection.commit()

    def _migrate_downloaded_posts_to_markers(self) -> None:
        path = self.marker_database()
        self._ensure_marker_schema(path)

        with self.connect() as con:
            rows = con.execute(
                """
                SELECT po.shortcode, p.username, po.post_url
                FROM posts po
                JOIN profiles p ON p.id = po.profile_id
                WHERE po.status = 'downloaded'
                """
            ).fetchall()

        if not rows:
            return

        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO downloads(shortcode, username, post_url)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        str(row["shortcode"]),
                        str(row["username"]),
                        str(row["post_url"]),
                    )
                    for row in rows
                ],
            )
            connection.commit()

    @staticmethod
    def _read_marker_rows(path: Path) -> list[tuple]:
        if not path.exists():
            return []
        try:
            with sqlite3.connect(path, timeout=30) as connection:
                return connection.execute(
                    """
                    SELECT shortcode, username, post_url, destination, downloaded_at
                    FROM downloads
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

    def _insert_marker_rows(self, path: Path, rows: list[tuple]) -> None:
        if not rows:
            return
        self._ensure_marker_schema(path)
        with sqlite3.connect(path, timeout=30) as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO downloads(
                    shortcode, username, post_url, destination, downloaded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
