"""Streaming downloads with resume and progress.

Kept separate from the browser layer so it can be used on any direct URL:
hanime hands out ordinary MP4 links (pixeldrain et al.), which need nothing
but ``requests``.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .exceptions import DownloadError

__all__ = [
    "download_file",
    "download_links",
    "direct_url",
    "pick_link",
    "safe_name",
    "filename_for",
]

CHUNK_SIZE = 1 << 20
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXTENSION = re.compile(r"\.(mp4|mkv|webm|avi|mov)$", re.IGNORECASE)
_RESOLUTION = re.compile(r"(\d{3,4})")
# hanime hands out pixeldrain *pages*; the bytes live behind the file API.
_PIXELDRAIN_PAGE = re.compile(r"^https?://(?:www\.)?pixeldrain\.(?:com|net)/u/([A-Za-z0-9_-]+)/?$")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0"
)


def direct_url(url: str) -> str:
    """Turn a share page into a direct file URL (pixeldrain ``/u/x`` -> API)."""
    match = _PIXELDRAIN_PAGE.match(str(url).strip())
    if match:
        return "https://pixeldrain.com/api/file/%s?download" % match.group(1)
    return url


def safe_name(text: str, fallback: str = "video", limit: int = 120) -> str:
    """Make ``text`` usable as a Windows/POSIX file name."""
    cleaned = _ILLEGAL.sub(" ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or fallback)[:limit].strip(" .") or fallback


def guess_extension(url: str) -> str:
    """Pick a file extension from a download URL."""
    path = str(url).split("?", 1)[0].split("#", 1)[0]
    match = _EXTENSION.search(path)
    return match.group(0).lower() if match else ".mp4"


def _resolution(link: Dict[str, Any]) -> int:
    for key in ("quality", "label", "name", "height", "resolution"):
        value = link.get(key)
        if value in (None, ""):
            continue
        match = _RESOLUTION.search(str(value))
        if match:
            return int(match.group(1))
    match = _RESOLUTION.search(str(link.get("url") or ""))
    return int(match.group(1)) if match else 0


def _quality_number(quality: Any) -> Optional[int]:
    if quality in (None, ""):
        return None
    match = _RESOLUTION.search(str(quality))
    return int(match.group(1)) if match else None


def _stderr_warning(message: str) -> None:
    sys.stderr.write("note: %s\n" % message)
    sys.stderr.flush()


def pick_link(
    links: List[Dict[str, Any]], quality: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Choose the best link, optionally filtered by a resolution such as ``"720"``."""
    candidates = [link for link in (links or []) if isinstance(link, dict) and link.get("url")]
    if not candidates:
        return None
    if quality:
        wanted = str(quality).lower().rstrip("p")
        explicit = [link for link in candidates if _resolution(link) == int(wanted)] if wanted.isdigit() else []
        if explicit:
            candidates = explicit
    return max(candidates, key=_resolution)


def filename_for(name: str, quality: Any, url: str) -> str:
    """``"Example Video [720p].mp4"``."""
    extension = guess_extension(url)
    label = str(quality or "").strip()
    if label and not label.lower().endswith("p"):
        label = "%sp" % label
    stem = safe_name(name)
    return "%s [%s]%s" % (stem, label, extension) if label else stem + extension


class _Progress:
    """Minimal stderr progress line; silent when not attached to a terminal."""

    def __init__(self, total: int, label: str, enabled: bool) -> None:
        self.total = total
        self.label = label
        self.enabled = bool(enabled) and sys.stderr.isatty()
        self.done = 0
        self.started = time.time()

    def update(self, chunk: int) -> None:
        self.done += chunk
        if not self.enabled:
            return
        elapsed = max(time.time() - self.started, 1e-6)
        rate = self.done / elapsed
        mb = self.done / 1048576.0
        if self.total:
            percent = min(100.0, self.done * 100.0 / self.total)
            size = "%5.1f/%.1f MB" % (mb, self.total / 1048576.0)
            bar = "%3.0f%%" % percent
        else:
            percent = 0.0
            size = "%5.1f MB" % mb
            bar = " ?? "
        line = "\r  %-34s %s %s  %5.1f MB/s" % (self.label[:34], bar, size, rate / 1048576.0)
        sys.stderr.write(line[:120])
        sys.stderr.flush()

    def finish(self) -> None:
        if not self.enabled:
            return
        elapsed = max(time.time() - self.started, 1e-6)
        sys.stderr.write(
            "\r  %-34s 100%% %5.1f MB  %5.1f MB/s   \n"
            % (self.label[:34], self.done / 1048576.0, (self.done / elapsed) / 1048576.0)
        )
        sys.stderr.flush()


def download_file(
    url: str,
    destination: os.PathLike,
    *,
    resume: bool = True,
    chunk_size: int = CHUNK_SIZE,
    timeout: float = 120.0,
    progress: bool = True,
    session: Optional[requests.Session] = None,
) -> Path:
    """Download ``url`` to ``destination``, resuming a ``.part`` file when possible."""
    url = direct_url(url)
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if (resume and part.exists()) else 0

    headers = {"user-agent": USER_AGENT, "accept": "*/*"}
    if have:
        headers["range"] = "bytes=%d-" % have

    http = session or requests
    try:
        response = http.get(url, headers=headers, stream=True, timeout=timeout)
    except requests.RequestException as exc:
        raise DownloadError("could not reach %s: %s" % (url, exc)) from exc

    with response:
        if response.status_code == 416 and have:
            os.replace(part, dest)
            return dest
        if response.status_code == 206 and have:
            mode = "ab"
        elif response.status_code == 200:
            mode, have = "wb", 0
        else:
            raise DownloadError(
                "download failed: HTTP %d for %s" % (response.status_code, url)
            )

        length = int(response.headers.get("content-length") or 0)
        total = have + length if length else 0
        meter = _Progress(total, dest.name, progress)

        try:
            with open(part, mode) as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    meter.update(len(chunk))
        except (requests.RequestException, OSError) as exc:
            raise DownloadError(
                "interrupted after %.1f MB (%s); rerun to resume"
                % (meter.done / 1048576.0, exc)
            ) from exc

    if total and part.stat().st_size != total:
        raise DownloadError(
            "incomplete download: %d of %d bytes" % (part.stat().st_size, total)
        )

    meter.finish()
    os.replace(part, dest)
    return dest


def download_links(
    links: List[Dict[str, Any]],
    name: str,
    out_dir: os.PathLike = ".",
    *,
    quality: Optional[str] = None,
    premium: bool = False,
    resume: bool = True,
    progress: bool = True,
    warn: Optional[Any] = _stderr_warning,
) -> Path:
    """Pick the requested quality from ``links`` and download it.

    When the requested resolution is not among the links, the closest one is
    used and ``warn`` is called with an explanation (``None`` silences it).
    """
    link = pick_link(links, quality)
    if link is None:
        raise DownloadError("no downloadable links were returned")

    available = [res for res in (_resolution(item) for item in links) if res]
    chosen_res = _resolution(link)
    wanted = _quality_number(quality)
    if warn and wanted and chosen_res and wanted != chosen_res:
        top = max(available) if available else chosen_res
        if wanted > top and not premium:
            warn(
                "%dp is not available on a free account (the highest offered here "
                "is %dp); using %dp" % (wanted, top, chosen_res)
            )
        else:
            warn("%dp is not offered for this video; using %dp" % (wanted, chosen_res))

    chosen = chosen_res or quality or ""
    target = Path(out_dir) / filename_for(name, chosen, link["url"])
    return download_file(link["url"], target, resume=resume, progress=progress)
