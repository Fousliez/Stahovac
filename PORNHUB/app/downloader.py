from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path


class PornhubDownloadError(RuntimeError):
    pass


QUALITY_FORMATS = {
    # "best" je schválně stejný formát jako u ručně ověřeného
    # funkčního příkazu.
    "best": "best[protocol=https][ext=mp4]/best",
    "1080": "best[height<=1080][protocol=https][ext=mp4]/best[height<=1080]/best",
    "720": "best[height<=720][protocol=https][ext=mp4]/best[height<=720]/best",
}

_PROGRESS_RE = re.compile(r"__STAHOVAC_PROGRESS__\s*([0-9]+(?:\.[0-9]+)?)%")
_TITLE_PREFIX = "__STAHOVAC_TITLE__"


def download_url(
    url: str,
    destination: str,
    archive_file: str,
    quality: str = "best",
    cookies_file: str = "",
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> str:
    """Stáhne URL přes stejný CLI režim yt-dlp, který je ověřený ručně.

    Záměrně nepoužíváme Python YoutubeDL API. Pornhub momentálně vyžaduje
    browser impersonaci a CLI cesta se na cílovém systému chová spolehlivě.
    """
    target = Path(destination).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    archive = Path(archive_file).expanduser()
    archive.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        "--newline",
        "--progress",
        "--impersonate",
        "Chrome-145:Macos-26",
        "-f",
        QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
        "--download-archive",
        str(archive),
        "-P",
        str(target),
        "-o",
        "%(uploader|Neznamy)s/%(title)s [%(id)s].%(ext)s",
        "--no-overwrites",
        "--continue",
        "--add-header",
        "Referer:https://www.pornhub.com/",
        "--print",
        f"before_dl:{_TITLE_PREFIX}%(title)s",
        "--progress-template",
        "download:__STAHOVAC_PROGRESS__%(progress._percent_str)s",
    ]

    if cookies_file.strip():
        cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])

    cmd.append(url)

    try:
        process = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            errors="replace",
        )
    except OSError as exc:
        raise PornhubDownloadError(f"yt-dlp se nepodařilo spustit: {exc}") from exc

    recent_output: deque[str] = deque(maxlen=120)
    final_title = ""
    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue

        recent_output.append(line)

        if line.startswith(_TITLE_PREFIX):
            title = line[len(_TITLE_PREFIX):].strip()
            if title:
                final_title = title
                if progress_callback is not None:
                    progress_callback(0, "downloading", final_title)
            continue

        match = _PROGRESS_RE.search(line)
        if match:
            try:
                percent = max(0, min(100, int(float(match.group(1)))))
            except ValueError:
                percent = 0
            if progress_callback is not None:
                progress_callback(percent, "downloading", final_title)

    returncode = process.wait()
    if returncode != 0:
        message = "\n".join(recent_output).strip() or f"yt-dlp skončil s kódem {returncode}."
        raise PornhubDownloadError(message)

    if progress_callback is not None:
        progress_callback(100, "finished", final_title)

    return final_title
