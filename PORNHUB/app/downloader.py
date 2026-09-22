from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class PornhubDownloadError(RuntimeError):
    pass


QUALITY_FORMATS = {
    # Pornhub má aktuálně problém s HLS/m3u8 (HTTP 410), zatímco
    # přímé MP4/HTTPS formáty často zůstávají dostupné. Proto je
    # záměrně vybíráme před HLS.
    "best": (
        "best[protocol=https][ext=mp4]/"
        "best[protocol=http][ext=mp4]/"
        "best[protocol=https]/"
        "best[protocol=http]/best"
    ),
    "1080": (
        "best[height<=1080][protocol=https][ext=mp4]/"
        "best[height<=1080][protocol=http][ext=mp4]/"
        "best[height<=1080][protocol=https]/"
        "best[height<=1080][protocol=http]/"
        "best[height<=1080]/best"
    ),
    "720": (
        "best[height<=720][protocol=https][ext=mp4]/"
        "best[height<=720][protocol=http][ext=mp4]/"
        "best[height<=720][protocol=https]/"
        "best[height<=720][protocol=http]/"
        "best[height<=720]/best"
    ),
}


def download_url(
    url: str,
    destination: str,
    archive_file: str,
    quality: str = "best",
    cookies_file: str = "",
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> str:
    target = Path(destination).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    archive = Path(archive_file).expanduser()
    archive.parent.mkdir(parents=True, exist_ok=True)

    final_title = ""

    def hook(data: dict) -> None:
        nonlocal final_title
        info = data.get("info_dict") or {}
        title = str(info.get("title") or info.get("id") or "").strip()
        if title:
            final_title = title

        status = str(data.get("status") or "")
        percent = 0
        if status == "finished":
            percent = 100
        elif status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            if total > 0:
                percent = max(0, min(100, int(downloaded * 100 / total)))

        if progress_callback is not None:
            progress_callback(percent, status, title)

    options = {
        "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
        "paths": {"home": str(target)},
        "outtmpl": {"default": "%(uploader|Neznamy)s/%(title)s [%(id)s].%(ext)s"},
        "download_archive": str(archive),
        "continuedl": True,
        "ignoreerrors": False,
        "nooverwrites": True,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": False,
        "progress_hooks": [hook],
        # Pornhub aktuálně vrací HTTP 410 běžným automatizovaným HTTP
        # klientům. curl_cffi dovolí yt-dlp posílat požadavky s browser
        # TLS fingerprintem; Chrome impersonace se osvědčila i v upstreamu.
        "impersonate": "Chrome-145:Macos-26",
        "http_headers": {
            "Referer": "https://www.pornhub.com/",
        },
    }
    if cookies_file.strip():
        options["cookiefile"] = str(Path(cookies_file).expanduser())

    try:
        with YoutubeDL(options) as ydl:
            code = ydl.download([url])
            if code not in (0, None):
                raise PornhubDownloadError(f"yt-dlp skončil s kódem {code}.")
    except DownloadError as exc:
        raise PornhubDownloadError(str(exc)) from exc
    except OSError as exc:
        raise PornhubDownloadError(str(exc)) from exc

    return final_title
