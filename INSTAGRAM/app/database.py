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
                    first_scan_done INTEGER NOT NULL DEFAULT 0,
                    checked INTEGER NOT NULL DEFAULT 0
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
            columns = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(profiles)").fetchall()
            }
            if "checked" not in columns:
                con.execute(
                    "ALTER TABLE profiles ADD COLUMN checked INTEGER NOT NULL DEFAULT 0"
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

    def set_profile_checked(self, profile_id: int, checked: bool) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE profiles SET checked = ? WHERE id = ?",
                (1 if checked else 0, int(profile_id)),
            )

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

    def register_scan(self, profile_id: int, posts: list[dict]) -> tuple[int, int]:
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
            initial_status = "known" if first_scan else "new"
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
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
