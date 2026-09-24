from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from datetime import datetime, timedelta
from collections.abc import Callable
from pathlib import Path


class PornhubDownloadError(RuntimeError):
    pass


BEST_FORMAT = "best[protocol=https][ext=mp4]/best"
NETWORK_ARGS = [
    "--socket-timeout",
    "60",
    "--retries",
    "5",
    "--extractor-retries",
    "5",
    "--retry-sleep",
    "2",
]

_PROGRESS_RE = re.compile(r"__STAHOVAC_PROGRESS__\s*([0-9]+(?:\.[0-9]+)?)%")
_ITEM_PREFIX = "__STAHOVAC_ITEM__"
_DONE_PREFIX = "__STAHOVAC_DONE__"


def resolve_reference_cutoff(
    reference_url: str,
    cookies_file: str = "",
) -> tuple[int, str]:
    """Vrátí přesný timestamp a záložní datum pro referenční video."""
    reference = str(reference_url or "").strip()
    if not reference:
        return 0, ""

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        *NETWORK_ARGS,
        "--skip-download",
        "--no-playlist",
        "--impersonate",
        "Chrome-145:Macos-26",
        "--add-header",
        "Referer:https://www.pornhub.com/",
        "--print",
        "%(timestamp|0)s\t%(upload_date|)s",
    ]
    if cookies_file.strip():
        cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])
    cmd.append(reference)

    try:
        process = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PornhubDownloadError(
            f"Referenční video se nepodařilo načíst: {exc}"
        ) from exc

    if process.returncode != 0:
        message = (
            process.stderr.strip()
            or process.stdout.strip()
            or "Referenční video se nepodařilo načíst."
        )
        raise PornhubDownloadError(message)

    line = next(
        (value.strip() for value in process.stdout.splitlines() if value.strip()),
        "",
    )
    parts = line.split("\t", 1)

    try:
        timestamp = int(float(parts[0])) if parts and parts[0] else 0
    except ValueError:
        timestamp = 0

    upload_date = parts[1].strip() if len(parts) > 1 else ""
    if upload_date and not re.fullmatch(r"\d{8}", upload_date):
        upload_date = ""

    if not timestamp and not upload_date:
        raise PornhubDownloadError(
            "U referenčního videa se nepodařilo zjistit datum ani čas zveřejnění."
        )

    if not timestamp and upload_date:
        # --dateafter je inkluzivní. Posun o jeden den tedy znamená
        # skutečně až videa z následujících dnů.
        day = datetime.strptime(upload_date, "%Y%m%d") + timedelta(days=1)
        upload_date = day.strftime("%Y%m%d")

    return timestamp, upload_date



def scan_url_items(
    url: str,
    cookies_file: str = "",
) -> list[dict]:
    """Projede profil/seznam bez stahování a vrátí současná video ID."""
    source = str(url or "").strip()
    if not source:
        return []

    prefix = "__STAHOVAC_SCAN__"
    base_cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        *NETWORK_ARGS,
        "--skip-download",
        "--flat-playlist",
        "--impersonate",
        "Chrome-145:Macos-26",
        "--add-header",
        "Referer:https://www.pornhub.com/",
        "--print",
        f"{prefix}%(id)s\t%(extractor_key|pornhub)s\t%(title|)s\t%(webpage_url|)s",
    ]
    if cookies_file.strip():
        base_cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])
    base_cmd.append(source)

    process = None
    last_message = ""
    for attempt in range(2):
        cmd = list(base_cmd)
        if attempt == 1:
            # Druhý pokus dostane ještě delší timeout. Pornhub bývá občas
            # línější než web státní správy, ale není důvod kvůli tomu vzdát scan.
            timeout_index = cmd.index("--socket-timeout") + 1
            cmd[timeout_index] = "90"

        try:
            process = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=1800,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            last_message = "Celá kontrola překročila časový limit."
            continue
        except OSError as exc:
            raise PornhubDownloadError(
                f"Pornhub kontrolu se nepodařilo spustit: {exc}"
            ) from exc

        if process.returncode == 0:
            break

        last_message = (
            process.stderr.strip()
            or process.stdout.strip()
            or "Profil nebo seznam se nepodařilo projít."
        )
        is_timeout = (
            "timed out" in last_message.casefold()
            or "timeout" in last_message.casefold()
            or "curl: (28)" in last_message.casefold()
        )
        if not is_timeout:
            break

    if process is None or process.returncode != 0:
        lowered = last_message.casefold()
        if (
            "timed out" in lowered
            or "timeout" in lowered
            or "curl: (28)" in lowered
        ):
            raise PornhubDownloadError(
                "Pornhub neodpověděl ani po opakovaném pokusu. "
                "Kontrola nic nezměnila. Zkus akci znovu za chvíli."
            )
        raise PornhubDownloadError(
            last_message or "Profil nebo seznam se nepodařilo projít."
        )

    items: dict[str, dict] = {}
    for raw_line in process.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix):].split("\t", 3)
        video_id = parts[0].strip() if parts else ""
        if not video_id:
            continue
        items.setdefault(
            video_id,
            {
                "id": video_id,
                "extractor": "pornhub",
                "title": parts[2].strip() if len(parts) > 2 else "",
                "webpage_url": parts[3].strip() if len(parts) > 3 else "",
            },
        )

    if not items:
        raise PornhubDownloadError(
            "Profil nebo seznam se podařilo otevřít, ale nenašel jsem žádná videa."
        )

    return list(items.values())


def download_url(
    url: str,
    destination: str,
    archive_file: str,
    cookies_file: str = "",
    progress_callback: Callable[[int, str, str, int, int], None] | None = None,
    completed_callback: Callable[[dict], None] | None = None,
    reference_timestamp: int = 0,
    reference_date_after: str = "",
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
        *NETWORK_ARGS,
        "--newline",
        "--progress",
        "--impersonate",
        "Chrome-145:Macos-26",
        "-f",
        BEST_FORMAT,
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
        f"before_dl:{_ITEM_PREFIX}%(playlist_index|1)s\t%(playlist_count|1)s\t%(title)s",
        "--progress-template",
        "download:__STAHOVAC_PROGRESS__%(progress._percent_str)s",
        "--print",
        (
            f"after_move:{_DONE_PREFIX}%(id)s\t%(extractor_key)s\t"
            "%(webpage_url)s\t%(uploader|)s\t%(title)s\t%(filepath)s"
        ),
    ]

    if cookies_file.strip():
        cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])

    if reference_timestamp > 0:
        cmd.extend(["--match-filter", f"timestamp>{int(reference_timestamp)}"])
    elif reference_date_after:
        cmd.extend(["--dateafter", reference_date_after])

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
    current_video_index = 1
    current_video_total = 1
    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue

        recent_output.append(line)

        if line.startswith(_ITEM_PREFIX):
            payload = line[len(_ITEM_PREFIX):]
            parts = payload.split("\t", 2)
            try:
                current_video_index = max(1, int(parts[0]))
            except (ValueError, IndexError):
                current_video_index = 1
            try:
                current_video_total = max(1, int(parts[1]))
            except (ValueError, IndexError):
                current_video_total = 1
            title = parts[2].strip() if len(parts) > 2 else ""
            if title:
                final_title = title
            if progress_callback is not None:
                progress_callback(
                    0,
                    "downloading",
                    final_title,
                    current_video_index,
                    current_video_total,
                )
            continue

        if line.startswith(_DONE_PREFIX):
            payload = line[len(_DONE_PREFIX):]
            parts = payload.split("\t", 5)
            item = {
                "id": parts[0].strip() if len(parts) > 0 else "",
                "extractor": parts[1].strip() if len(parts) > 1 else "",
                "webpage_url": parts[2].strip() if len(parts) > 2 else "",
                "uploader": parts[3].strip() if len(parts) > 3 else "",
                "title": parts[4].strip() if len(parts) > 4 else "",
                "filepath": parts[5].strip() if len(parts) > 5 else "",
                "source_url": url,
            }
            if item["title"]:
                final_title = item["title"]
            if completed_callback is not None and item["id"]:
                completed_callback(item)
            continue

        match = _PROGRESS_RE.search(line)
        if match:
            try:
                percent = max(0, min(100, int(float(match.group(1)))))
            except ValueError:
                percent = 0
            if progress_callback is not None:
                progress_callback(
                    percent,
                    "downloading",
                    final_title,
                    current_video_index,
                    current_video_total,
                )

    returncode = process.wait()
    if returncode != 0:
        message = "\n".join(recent_output).strip() or f"yt-dlp skončil s kódem {returncode}."
        raise PornhubDownloadError(message)

    if progress_callback is not None:
        progress_callback(
            100,
            "finished",
            final_title,
            current_video_index,
            current_video_total,
        )

    return final_title
