from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,32}$")


class GalleryDLError(RuntimeError):
    pass


def executable() -> str:
    """Použij gallery-dl ze stejného virtuálního prostředí jako aplikace.

    start_app.sh spouští Python přímo z .venv, ale virtuální prostředí
    neaktivuje do PATH. shutil.which() proto dříve mohl najít starou systémovou
    instalaci gallery-dl místo verze nainstalované v .venv.
    """
    python_dir = Path(sys.executable).resolve().parent
    for name in ("gallery-dl", "gallery-dl.exe"):
        candidate = python_dir / name
        if candidate.is_file():
            return str(candidate)

    path = shutil.which("gallery-dl")
    if not path:
        raise GalleryDLError(
            "gallery-dl není nainstalovaný. Spusť: pip install -r requirements.txt"
        )
    return path


def _cookies_args(cookies_file: str) -> list[str]:
    cookies = str(cookies_file or "").strip()
    if not cookies:
        return []
    path = Path(cookies).expanduser()
    if not path.exists():
        raise GalleryDLError(f"Soubor cookies neexistuje: {path}")
    return ["--cookies", str(path)]


def scan_profile(profile_url: str, cookies_file: str = "") -> list[dict]:
    """Projede Instagram profil bez stahování a vrátí unikátní posty.

    gallery-dl vypisuje metadata pro každý soubor. Carousel proto může stejný
    post vypsat několikrát; deduplikujeme jej podle post_shortcode.
    """
    cmd = [
        executable(),
        "--simulate",
        "--no-colors",
        "--print",
        "{post_shortcode}",
        *_cookies_args(cookies_file),
        profile_url,
    ]
    process = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1800,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "Neznámá chyba gallery-dl"
        raise GalleryDLError(message)

    posts: dict[str, dict] = {}
    for raw_line in process.stdout.splitlines():
        shortcode = raw_line.strip()
        if not SHORTCODE_RE.match(shortcode):
            continue
        posts.setdefault(
            shortcode,
            {
                "shortcode": shortcode,
                "post_url": f"https://www.instagram.com/p/{shortcode}/",
            },
        )

    if not posts:
        raise GalleryDLError(
            "Profil se podařilo spustit, ale nenašel jsem žádné příspěvky. "
            "Nejčastěji jsou potřeba aktuální Instagram cookies."
        )
    return list(posts.values())


def download_post(post_url: str, destination: str, cookies_file: str = "") -> None:
    target = Path(destination).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    cmd = [
        executable(),
        "--no-colors",
        "--directory",
        str(target),
        *_cookies_args(cookies_file),
        post_url,
    ]
    process = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1800,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "Stahování selhalo"
        raise GalleryDLError(message)
