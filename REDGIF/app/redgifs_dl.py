from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


GIF_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


class RedGIFError(RuntimeError):
    pass


def gallery_dl_command() -> list[str]:
    return [sys.executable, "-m", "gallery_dl"]


def _run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RedGIFError("Operace trvala příliš dlouho a byla ukončena.") from exc

    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "Neznámá chyba gallery-dl"
        raise RedGIFError(message)
    return process


def scan_profile(profile_url: str) -> list[dict]:
    """Vrátí unikátní RedGIF ID nalezená na uživatelském profilu."""
    cmd = [
        *gallery_dl_command(),
        "--simulate",
        "--no-colors",
        "--no-input",
        "--print",
        "{id}",
        profile_url,
    ]
    process = _run(cmd)

    items: dict[str, dict] = {}
    for raw_line in process.stdout.splitlines():
        gif_id = raw_line.strip()
        if not GIF_ID_RE.fullmatch(gif_id):
            continue
        key = gif_id.lower()
        items.setdefault(
            key,
            {
                "id": gif_id,
                "url": f"https://www.redgifs.com/watch/{gif_id}",
            },
        )

    if not items:
        raise RedGIFError(
            "Profil se podařilo otevřít, ale nebyl nalezen žádný RedGIF. "
            "Profil může být prázdný, nedostupný nebo RedGIFs změnil API."
        )
    return list(items.values())


def download_gif(gif_id: str, post_url: str, destination: str) -> None:
    """Stáhne právě jeden RedGIF v nejlepší dostupné kvalitě."""
    if not GIF_ID_RE.fullmatch(gif_id):
        raise RedGIFError(f"Neplatné RedGIF ID: {gif_id}")

    target = Path(destination).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    # Některý RedGIF může patřit do galerie. Filtr zajistí, že se při
    # otevření watch URL stáhne jen konkrétní ID a ne celá galerie.
    cmd = [
        *gallery_dl_command(),
        "--no-colors",
        "--no-input",
        "--directory",
        str(target),
        "--filename",
        "{id}.{extension}",
        "--filter",
        f"id == '{gif_id}'",
        post_url,
    ]
    _run(cmd)
