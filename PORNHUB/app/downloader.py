from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timedelta
from collections.abc import Callable
from html import unescape
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from curl_cffi import requests as curl_requests


class PornhubDownloadError(RuntimeError):
    pass


class DownloadCancelled(PornhubDownloadError):
    pass


def _send_process_signal(process: subprocess.Popen | None, sig: int) -> bool:
    if process is None or process.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        else:
            if sig == signal.SIGTERM:
                process.terminate()
            else:
                return False
        return True
    except (OSError, ProcessLookupError):
        return False


class DownloadControl:
    """Thread-safe ovládání běžícího yt-dlp procesu z GUI."""

    def __init__(self):
        self._lock = threading.RLock()
        self._processes: set[subprocess.Popen] = set()
        self._paused = False
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.add(process)
            cancelled = self._cancelled
            paused = self._paused

        if cancelled:
            _send_process_signal(process, signal.SIGTERM)
        elif paused and os.name == "posix":
            _send_process_signal(process, signal.SIGSTOP)

    def detach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def pause(self) -> bool:
        if os.name != "posix":
            return False

        with self._lock:
            if self._cancelled:
                return False
            self._paused = True
            processes = list(self._processes)

        if not processes:
            return True
        results = [_send_process_signal(process, signal.SIGSTOP) for process in processes]
        return any(results)

    def resume(self) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            was_paused = self._paused
            self._paused = False
            processes = list(self._processes)

        if not was_paused:
            return True
        if not processes:
            return True
        if os.name != "posix":
            return False
        results = [_send_process_signal(process, signal.SIGCONT) for process in processes]
        return any(results)

    def cancel(self) -> bool:
        with self._lock:
            already_cancelled = self._cancelled
            self._cancelled = True
            was_paused = self._paused
            self._paused = False
            processes = list(self._processes)

        if already_cancelled:
            return True
        if not processes:
            return True

        if was_paused and os.name == "posix":
            for process in processes:
                _send_process_signal(process, signal.SIGCONT)
        for process in processes:
            _send_process_signal(process, signal.SIGTERM)

        if os.name == "posix":
            def force_kill():
                threading.Event().wait(3)
                for process in processes:
                    if process.poll() is None:
                        _send_process_signal(process, signal.SIGKILL)

            threading.Thread(target=force_kill, daemon=True).start()
        return True


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
_PROFILE_SOURCE_RE = re.compile(
    r"/(?:(?:user|channel)s|model|pornstar)/[^/?#]+(?:/videos(?:/(?:public|upload))?)?/?$",
    re.I,
)


def is_profile_source_url(url: str) -> bool:
    try:
        path = urlparse(str(url or "")).path
    except ValueError:
        return False
    return bool(_PROFILE_SOURCE_RE.search(path))


def _profile_videos_url(profile_path: str, source_url: str) -> str:
    path = str(profile_path or "").strip()
    if not path.startswith("/"):
        return ""
    path = path.rstrip("/") + "/videos"

    try:
        parsed = urlparse(str(source_url or ""))
    except ValueError:
        parsed = urlparse("https://www.pornhub.com/")

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.pornhub.com"
    return urlunparse((scheme, netloc, path, "", "", ""))


def _normalized_source_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return str(url or "").strip().casefold()
    return f"{parsed.netloc.casefold()}{parsed.path.rstrip('/').casefold()}"


def _cookies_for_request(cookies_file: str = "") -> dict[str, str]:
    cookies = {
        "age_verified": "1",
        "accessAgeDisclaimerPH": "1",
        "accessPH": "1",
    }
    path = str(cookies_file or "").strip()
    if not path:
        return cookies

    try:
        jar = MozillaCookieJar()
        jar.load(str(Path(path).expanduser()), ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            cookies[cookie.name] = cookie.value
    except (OSError, ValueError):
        # Cookies jsou pro veřejná videa volitelné. Chybný nebo zastaralý
        # soubor nesmí zablokovat samotnou obnovu profilu.
        pass
    return cookies


def extract_profile_identity_from_video(
    video_url: str,
    cookies_file: str = "",
) -> dict:
    """Z videa vytáhne interní Pornhub user_id a aktuální profilový odkaz."""
    url = str(video_url or "").strip()
    if not url:
        raise PornhubDownloadError("Chybí odkaz na záložní video.")

    try:
        response = curl_requests.get(
            url,
            impersonate="chrome",
            headers={"Referer": "https://www.pornhub.com/"},
            cookies=_cookies_for_request(cookies_file),
            timeout=60,
        )
    except Exception as exc:
        raise PornhubDownloadError(
            f"Záložní video se nepodařilo otevřít: {exc}"
        ) from exc

    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise PornhubDownloadError(
            f"Záložní video vrátilo HTTP {response.status_code}."
        )

    html = str(getattr(response, "text", "") or "")
    if not html:
        raise PornhubDownloadError("Záložní video vrátilo prázdnou stránku.")

    profile_name = ""
    profile_path = ""
    model_match = re.search(
        r"var\s+MODEL_PROFILE\s*=\s*(\{.*?\});",
        html,
        re.S,
    )
    if model_match:
        try:
            model_profile = json.loads(model_match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            model_profile = {}
        profile_name = str(model_profile.get("username") or "").strip()
        profile_path = str(model_profile.get("modelProfileLink") or "").strip()

    user_id = ""
    up_id_match = re.search(
        r"""['"]up_id['"]\s*:\s*['"](\d+)['"]""",
        html,
        re.I,
    )
    if up_id_match:
        user_id = up_id_match.group(1)

    if not user_id and profile_path:
        linked_id = re.search(
            rf"""data-userid=['"](\d+)['"][^>]*>[\s\S]{{0,1800}}?
                 href=['"]{re.escape(profile_path)}['"]""",
            html,
            re.I | re.X,
        )
        if linked_id:
            user_id = linked_id.group(1)

    if not user_id:
        generic_id = re.search(r"""data-userid=['"](\d+)['"]""", html, re.I)
        if generic_id:
            user_id = generic_id.group(1)

    if not profile_path:
        linked_profile = re.search(
            r"""href=['"](?P<path>/(?:(?:user|channel)s|model|pornstar)/[^'"]+)['"]
                [^>]*>(?P<name>[^<]+)<""",
            html,
            re.I | re.X,
        )
        if linked_profile:
            profile_path = linked_profile.group("path")
            if not profile_name:
                profile_name = unescape(linked_profile.group("name")).strip()

    if not user_id or not profile_path:
        raise PornhubDownloadError(
            "U záložního videa se nepodařilo zjistit interní ID a profil autora."
        )

    return {
        "user_id": user_id,
        "profile_name": unescape(profile_name).strip(),
        "profile_path": profile_path,
        "source_video_url": url,
    }


def _video_url_from_item(item: dict) -> str:
    webpage_url = str(item.get("webpage_url") or "").strip()
    if "view_video.php" in webpage_url.casefold():
        return webpage_url
    video_id = str(item.get("id") or "").strip()
    if not video_id:
        return ""
    return f"https://www.pornhub.com/view_video.php?viewkey={video_id}"


def scan_source_with_identity(
    url: str,
    cookies_file: str = "",
    profile_meta: dict | None = None,
) -> tuple[list[dict], dict]:
    """Projede zdroj a při rozbité profilové URL zkusí dohledat přejmenování."""
    source = str(url or "").strip()
    meta = profile_meta if isinstance(profile_meta, dict) else {}
    expected_user_id = str(meta.get("ph_user_id") or "").strip()
    recovery_videos = meta.get("recovery_videos") or []
    if not isinstance(recovery_videos, list):
        recovery_videos = []

    try:
        items = scan_url_items(source, cookies_file)
    except Exception as original_exc:
        if not (is_profile_source_url(source) and expected_user_id and recovery_videos):
            raise

        checked = 0
        for recovery_url in recovery_videos[:30]:
            recovery_url = str(recovery_url or "").strip()
            if not recovery_url:
                continue
            checked += 1
            try:
                identity = extract_profile_identity_from_video(
                    recovery_url,
                    cookies_file,
                )
            except Exception:
                continue

            if str(identity.get("user_id") or "") != expected_user_id:
                continue

            new_url = _profile_videos_url(
                str(identity.get("profile_path") or ""),
                source,
            )
            if not new_url or _normalized_source_url(new_url) == _normalized_source_url(source):
                continue

            try:
                recovered_items = scan_url_items(new_url, cookies_file)
            except Exception:
                continue

            identity.update(
                {
                    "renamed": True,
                    "old_url": source,
                    "new_url": new_url,
                    "recovered_from": recovery_url,
                }
            )
            return recovered_items, identity

        raise PornhubDownloadError(
            f"{original_exc}\n\n"
            f"Automatická kontrola přejmenování zkusila {checked} uložených videí, "
            "ale nový profil se nepodařilo bezpečně potvrdit."
        ) from original_exc

    identity: dict = {}
    if is_profile_source_url(source) and not expected_user_id:
        for item in items[:3]:
            recovery_url = _video_url_from_item(item)
            if not recovery_url:
                continue
            try:
                identity = extract_profile_identity_from_video(
                    recovery_url,
                    cookies_file,
                )
            except Exception:
                continue
            break

    return items, identity


def resolve_reference_cutoff(
    reference_url: str,
    cookies_file: str = "",
    control: DownloadControl | None = None,
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
    if use_archive:
        # V běžném sekvenčním režimu necháváme ochranu proti duplicitám i na
        # yt-dlp. Paralelní režim vybírá pouze DB-ově nové položky, takže se
        # vyhne dvěma procesům zapisujícím současně do stejného archive souboru.
        insert_at = cmd.index("-P")
        cmd[insert_at:insert_at] = ["--download-archive", str(archive)]

    if cookies_file.strip():
        cmd.extend(["--cookies", str(Path(cookies_file).expanduser())])
    cmd.append(reference)

    if control is not None and control.cancelled:
        raise DownloadCancelled("Stahování bylo zrušeno uživatelem.")

    try:
        process = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            errors="replace",
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise PornhubDownloadError(
            f"Referenční video se nepodařilo načíst: {exc}"
        ) from exc

    if control is not None:
        control.attach(process)

    try:
        try:
            stdout, stderr = process.communicate(timeout=180)
        except subprocess.TimeoutExpired as exc:
            _send_process_signal(process, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    _send_process_signal(process, signal.SIGKILL)
                else:
                    process.kill()
                stdout, stderr = process.communicate()
            raise PornhubDownloadError(
                "Referenční video překročilo časový limit."
            ) from exc
    finally:
        if control is not None:
            control.detach(process)

    if control is not None and control.cancelled:
        raise DownloadCancelled("Stahování bylo zrušeno uživatelem.")

    if process.returncode != 0:
        message = (
            stderr.strip()
            or stdout.strip()
            or "Referenční video se nepodařilo načíst."
        )
        raise PornhubDownloadError(message)

    line = next(
        (value.strip() for value in stdout.splitlines() if value.strip()),
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
    progress_callback: Callable[[int, str, str, str, int, int], None] | None = None,
    completed_callback: Callable[[dict], None] | None = None,
    reference_timestamp: int = 0,
    reference_date_after: str = "",
    control: DownloadControl | None = None,
    use_archive: bool = True,
    source_url_override: str = "",
) -> str:
    """Stáhne URL přes stejný CLI režim yt-dlp, který je ověřený ručně.

    Záměrně nepoužíváme Python YoutubeDL API. Pornhub momentálně vyžaduje
    browser impersonaci a CLI cesta se na cílovém systému chová spolehlivě.
    """
    if control is not None and control.cancelled:
        raise DownloadCancelled("Stahování bylo zrušeno uživatelem.")

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
    ]

    # U profilu/seznamu nesmí jedno dočasně nedostupné video shodit celou
    # frontu. Nedokončené video se nezapíše do markerů ani archivu, takže
    # při příštím spuštění zůstane mezi novými a zkusí se znovu.
    if "view_video.php" not in str(url or "").casefold():
        cmd.append("--ignore-errors")

    cmd.extend([
        "--newline",
        "--progress",
        "--impersonate",
        "Chrome-145:Macos-26",
        # Přímé MP4 necháváme na jednom stabilním HTTP spojení. Range chunking
        # některé Pornhub CDN uzly umí výrazně zdržet ještě před prvním bajtem.
        "-f",
        BEST_FORMAT,
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
        "download:__STAHOVAC_PROGRESS__%(progress._percent_str)s\t%(progress._speed_str)s",
        "--print",
        (
            f"after_move:{_DONE_PREFIX}%(id)s\t%(extractor_key)s\t"
            "%(webpage_url)s\t%(uploader|)s\t%(title)s\t%(filepath)s"
        ),
    ])

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
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise PornhubDownloadError(f"yt-dlp se nepodařilo spustit: {exc}") from exc

    if control is not None:
        control.attach(process)

    recent_output: deque[str] = deque(maxlen=120)
    final_title = ""
    completed_any = False
    saw_video_error = False
    current_video_index = 1
    current_video_total = 1
    assert process.stdout is not None

    try:
        for raw_line in process.stdout:
            if control is not None and control.cancelled:
                break

            line = raw_line.rstrip()
            if not line:
                continue

            recent_output.append(line)

            # yt-dlp může u profilu narazit na soukromé, smazané nebo
            # dočasně nedostupné video. To je chyba jedné položky, ne
            # celého profilu. Poznáme ji podle konkrétního Pornhub ID.
            if re.match(r"^ERROR:\s+\[PornHub\]\s+\S+:", line):
                saw_video_error = True

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
                        "",
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
                    "source_url": str(source_url_override or url),
                }
                if item["title"]:
                    final_title = item["title"]
                if item["id"]:
                    completed_any = True
                    if completed_callback is not None:
                        completed_callback(item)
                continue

            match = _PROGRESS_RE.search(line)
            if match:
                try:
                    percent = max(0, min(100, int(float(match.group(1)))))
                except ValueError:
                    percent = 0

                speed = ""
                if "\t" in line:
                    speed = line.split("\t", 1)[1].strip()
                    if speed.casefold() in {"n/a", "na", "none"}:
                        speed = ""

                if progress_callback is not None:
                    progress_callback(
                        percent,
                        "downloading",
                        final_title,
                        speed,
                        current_video_index,
                        current_video_total,
                    )

        returncode = process.wait()
    finally:
        if control is not None:
            control.detach(process)

    if control is not None and control.cancelled:
        raise DownloadCancelled("Stahování bylo zrušeno uživatelem.")

    if returncode != 0:
        # U profilu může yt-dlp narazit na soukromé/smazané/dočasně
        # nedostupné video a skončit nenulovým kódem, i když všechna
        # dostupná videa už byla stažená nebo byla v archivu. Taková chyba
        # jedné položky nesmí shodit celý profil do stavu "Chyba".
        is_profile_or_list = "view_video.php" not in str(url or "").casefold()
        if not (is_profile_or_list and (completed_any or saw_video_error)):
            message = "\n".join(recent_output).strip() or f"yt-dlp skončil s kódem {returncode}."
            raise PornhubDownloadError(message)

    if progress_callback is not None:
        progress_callback(
            100,
            "finished",
            final_title,
            "",
            current_video_index,
            current_video_total,
        )

    return final_title
