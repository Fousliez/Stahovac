from __future__ import annotations

import json
from pathlib import Path


class Storage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_file = self.data_dir / "jobs.json"
        self.settings_file = self.data_dir / "settings.json"
        self.archive_file = self.data_dir / "download_archive.txt"

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
        self.save_jobs([job for job in self.jobs() if str(job.get("url", "")) not in wanted])

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
        data = self._read_json(self.settings_file, {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self._write_json(self.settings_file, data)
