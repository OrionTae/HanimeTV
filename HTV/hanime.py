"""HanimeTV v2 - client for the public hanime.tv API.

The v1 module targeted ``search.hanime.tv`` / ``members.hanime.tv`` /
``mapi.htvanime.com`` and API versions v4/v5. All three hosts were taken
offline; the site now serves a single public catalog from
``guest.freeanimehentai.net/api/v11`` and renders the per-video extras
(storyboards, poster) into the server-rendered watch page.

This module uses only those two public sources, so it needs no account.
Download links are intentionally unsupported: they require a signed-in
account and a handshake signed by the site's WebAssembly module.

Author: Moe Poi <moepoi@protonmail.com>
License: MIT
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    HanimeTVError,
    NetworkError,
    NotFound,
    ParseError,
    UnsupportedFeature,
)

__all__ = ["HanimeTV"]

__version__ = "2.2.1"

BASE_URL = "https://hanime.tv"
GUEST_API_URL = "https://guest.freeanimehentai.net"
CATALOG_URL = GUEST_API_URL + "/api/v11/search_hvs"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0"
)

# /videos/hentai/<slug> is the current scheme, /hentai-videos/<slug> the 2019 one.
_SLUG_IN_URL = re.compile(r"/(?:videos/hentai|hentai-videos)/([^/?#]+)")
_SLUG_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: ``order=`` keywords -> (catalog field, reverse?)  ``None`` = search relevance.
ORDERINGS = {
    "relevance": None,
    "newest": ("created_at_unix", True),
    "oldest": ("created_at_unix", False),
    "released": ("released_at_unix", True),
    "views": ("views", True),
    "likes": ("likes", True),
    "downloads": ("downloads", True),
    "name": ("name", False),
    "random": "random",
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _decode_props(raw: Optional[str]) -> Dict[str, Any]:
    """Decode an Astro island ``props`` attribute.

    Astro serialises each prop as ``[0, <value>]``; a missing attribute means
    "no props". Garbage is ignored rather than raising, because ad islands are
    not part of our contract.
    """
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    decoded: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, list) and len(value) == 2 and value[0] == 0:
            decoded[key] = value[1]
        else:
            decoded[key] = value
    return decoded


class _PageParser(HTMLParser):
    """Collect ``<astro-island>`` attributes, ``<title>`` and og/meta tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.islands: List[Dict[str, str]] = []
        self.meta: Dict[str, str] = {}
        self.title: str = ""
        self._in_title = False
        self._title_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        values = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "astro-island":
            self.islands.append(values)
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content")
            if name and content:
                self.meta[name] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def close(self) -> None:  # pragma: no cover - thin wrapper
        super().close()
        self.title = "".join(self._title_parts).strip()


def _storyboard(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the storyboard descriptor from merged page props."""
    url = data.get("storyboard_url")
    if not isinstance(url, str) or not url.strip():
        return None
    columns = _as_int(data.get("storyboard_columns"))
    rows = _as_int(data.get("storyboard_rows"))
    tiles = _as_int(data.get("storyboard_tile_count")) or columns * rows
    return {
        "url": url.strip(),
        "columns": columns,
        "rows": rows,
        "tile_count": tiles,
    }


class HanimeTV:
    """Client for the public hanime.tv API.

    Args:
        email: Accepted for backwards compatibility with v1 only. Logging in is
            no longer possible from a script, so passing credentials raises
            :class:`~HTV.exceptions.UnsupportedFeature`.
        password: See ``email``.
        timeout: Per-request timeout in seconds.
        cache_ttl: Seconds the catalog / watch pages stay cached.
        retries: Retries for idempotent GETs on connection errors and 5xx.
        session: Optional pre-configured :class:`requests.Session`.

    Example::

        from HTV import HanimeTV

        h = HanimeTV()
        for video in h.search("demo")[:5]:
            print(video["name"], video["url"])
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        *,
        timeout: float = 30.0,
        cache_ttl: float = 3600.0,
        retries: int = 2,
        session: Optional[requests.Session] = None,
    ) -> None:
        if email or password:
            raise UnsupportedFeature(
                "hanime.tv removed scripted login: sign-in now needs a Cloudflare "
                "Turnstile challenge, and every authenticated call is signed by a "
                "browser-only WebAssembly module. Construct HanimeTV() without "
                "credentials; search/info/storyboards need no account."
            )
        self.timeout = float(timeout)
        self.cache_ttl = float(cache_ttl)
        self.session = session or self._build_session(retries)
        self._catalog: Optional[List[Dict[str, Any]]] = None
        self._catalog_at = 0.0
        self._pages: "Dict[str, Tuple[float, Dict[str, Any]]]" = {}

    # ------------------------------------------------------------------ http

    def _build_session(self, retries: int) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "user-agent": DEFAULT_USER_AGENT,
                "accept-language": "en-US,en;q=0.5",
                "accept-encoding": "gzip, deflate",
            }
        )
        retry = Retry(
            total=max(0, int(retries)),
            connect=max(0, int(retries)),
            read=max(0, int(retries)),
            status=max(0, int(retries)),
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        expect_json: bool = False,
    ) -> Any:
        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise NetworkError("GET %s failed: %s" % (url, exc)) from exc

        if response.status_code == 404:
            raise NotFound("404 Not Found: %s" % url)
        if response.status_code >= 400:
            raise HanimeTVError(
                "GET %s -> HTTP %d" % (url, response.status_code)
            )
        if not expect_json:
            return response
        try:
            return response.json()
        except ValueError as exc:
            raise ParseError(
                "GET %s did not return JSON (%s)" % (url, response.headers.get("content-type"))
            ) from exc

    # --------------------------------------------------------------- catalog

    @staticmethod
    def _normalise(entry: Dict[str, Any]) -> Dict[str, Any]:
        video = dict(entry)
        slug = _clean_text(video.get("slug"))
        video["slug"] = slug
        video["url"] = "%s/videos/hentai/%s" % (BASE_URL, slug)
        video.setdefault("tags", [])
        return video

    def catalog(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """Return the full public catalog (one HTTP request, then cached).

        Each item is a plain dict with the keys ``id``, ``name``, ``slug``,
        ``url``, ``brand``, ``brand_id``, ``description``, ``tags``,
        ``views``, ``likes``, ``dislikes``, ``downloads``, ``created_at``,
        ``released_at``, ``poster_url``, ``cover_url`` and ``search_titles``.
        """
        fresh = (
            not refresh
            and self._catalog is not None
            and (time.time() - self._catalog_at) < self.cache_ttl
        )
        if fresh:
            return list(self._catalog)  # type: ignore[arg-type]

        payload = self._get(
            CATALOG_URL,
            headers={"accept": "application/json", "referer": BASE_URL + "/"},
            expect_json=True,
        )
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list) or not data:
            raise ParseError("unexpected catalog payload from %s" % CATALOG_URL)

        self._catalog = [self._normalise(item) for item in data if isinstance(item, dict)]
        self._catalog_at = time.time()
        if not self._catalog:
            raise ParseError("catalog contained no usable entries")
        return list(self._catalog)

    def refresh(self) -> List[Dict[str, Any]]:
        """Force a catalog re-download."""
        return self.catalog(refresh=True)

    # ---------------------------------------------------------------- search

    @staticmethod
    def _match(video: Dict[str, Any], query: str, tokens: List[str]) -> int:
        """Return a relevance score for ``video`` (0 = no match)."""
        name = _clean_text(video.get("name")).lower()
        titles = _clean_text(video.get("search_titles")).lower()
        slug = _clean_text(video.get("slug")).replace("-", " ")
        haystack = (name, titles, slug)
        if not all(any(token in field for field in haystack) for token in tokens):
            return 0
        if name == query:
            return 1000
        if name.startswith(query):
            return 900
        if query in name:
            return 800
        if query in slug:
            return 700
        return 600

    def search(
        self,
        query: str = "",
        *,
        page: int = 0,
        per_page: Optional[int] = 25,
        order: str = "relevance",
        tags: Optional[Iterable[str]] = None,
        brand: Optional[str] = None,
        all: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search the catalog locally (hanime.tv has no public search endpoint).

        Args:
            query: Free text, matched against title, alternate titles and slug.
                Use ``""`` to browse.
            page: Zero-based page number.
            per_page: Page size; ``None`` returns everything.
            order: One of :data:`ORDERINGS` (``"relevance"`` keeps the best
                textual match first, then most viewed).
            tags: Only keep videos carrying **all** of these tags.
            brand: Only keep videos from this brand (case-insensitive).
            all: Ignore ``page``/``per_page`` and return every match.

        Returns:
            A list of catalog dicts, each with a ready-to-use ``url``.
        """
        if order not in ORDERINGS:
            raise ValueError(
                "order must be one of %s" % ", ".join(sorted(ORDERINGS))
            )
        if page < 0:
            raise ValueError("page must be >= 0")
        if per_page is not None and per_page <= 0:
            raise ValueError("per_page must be > 0 or None")

        query = _clean_text(query)
        needle = query.lower()
        tokens = needle.split()

        results: List[Dict[str, Any]] = []
        scores: Dict[str, int] = {}

        wanted_tags = {_clean_text(t).lower() for t in (tags or []) if _clean_text(t)}
        wanted_brand = _clean_text(brand).lower()

        for video in self.catalog():
            if wanted_brand and _clean_text(video.get("brand")).lower() != wanted_brand:
                continue
            if wanted_tags:
                have = {_clean_text(t).lower() for t in video.get("tags") or []}
                if not wanted_tags.issubset(have):
                    continue
            if tokens:
                score = self._match(video, needle, tokens)
                if not score:
                    continue
                scores[video["slug"]] = score
            results.append(video)

        if order == "relevance":
            if tokens:
                results.sort(
                    key=lambda v: (scores.get(v.get("slug"), 0), _as_int(v.get("views"))),
                    reverse=True,
                )
            else:
                results.sort(key=lambda v: _as_int(v.get("views")), reverse=True)
        elif order == "random":
            random.shuffle(results)
        else:
            field, reverse = ORDERINGS[order]  # type: ignore[misc]
            results.sort(key=lambda v: _as_int(v.get(field)), reverse=reverse)

        if all or per_page is None:
            return results
        start = page * per_page
        return results[start : start + per_page]

    def tags(self) -> List[str]:
        """Every tag in the catalog, sorted."""
        seen = set()
        for video in self.catalog():
            for tag in video.get("tags") or []:
                tag = _clean_text(tag)
                if tag:
                    seen.add(tag)
        return sorted(seen)

    def brands(self) -> List[str]:
        """Every brand in the catalog, sorted."""
        return sorted(
            {
                _clean_text(v.get("brand"))
                for v in self.catalog()
                if _clean_text(v.get("brand"))
            }
        )

    # ----------------------------------------------------------------- pages

    @staticmethod
    def slug(url: Union[str, Dict[str, Any]]) -> str:
        """Extract a slug from a URL, a dict, or return a bare slug unchanged.

        Accepts ``https://hanime.tv/videos/hentai/<slug>``, the legacy
        ``/hentai-videos/<slug>`` form, ``/videos/hentai/<slug>`` and
        ``"<slug>"``.
        """
        if isinstance(url, dict):
            url = url.get("slug") or url.get("url") or ""
        if not isinstance(url, str):
            raise TypeError("expected str or dict, got %s" % type(url).__name__)

        value = url.strip()
        if not value:
            raise ValueError("empty URL or slug")

        # Anything with a path separator must be a real video URL, so that
        # "https://hanime.tv/" or "/videos/hentai/" can never yield a bogus slug.
        if "://" in value or "/" in value:
            match = _SLUG_IN_URL.search(value)
            if not match:
                raise ValueError(
                    "not a hanime.tv video URL (expected .../videos/hentai/<slug>): %r"
                    % url
                )
            value = match.group(1)

        if not _SLUG_VALUE.match(value):
            raise ValueError("not a hanime.tv slug: %r" % url)
        return value

    def _page_data(self, slug: str) -> Dict[str, Any]:
        """Fetch and flatten the watch page's Astro island props (cached)."""
        cached = self._pages.get(slug)
        if cached and (time.time() - cached[0]) < self.cache_ttl:
            return cached[1]

        url = "%s/videos/hentai/%s" % (BASE_URL, quote(slug, safe=""))
        response = self._get(url, headers={"accept": "text/html,*/*"})
        parser = _PageParser()
        try:
            parser.feed(response.text)
            parser.close()
        except Exception as exc:  # HTMLParser is lenient; this is belt and braces
            raise ParseError("could not parse %s: %s" % (url, exc)) from exc

        data: Dict[str, Any] = {}
        for island in parser.islands:
            data.update(_decode_props(island.get("props")))

        if parser.title:
            data.setdefault("title", parser.title)
        for key in ("og:image", "og:title", "description"):
            if parser.meta.get(key):
                data.setdefault(key, parser.meta[key])
        if data.get("og:image"):
            data.setdefault("og_image", data["og:image"])

        if not data:
            raise ParseError("no metadata found on %s" % url)

        if len(self._pages) >= 128:
            self._pages.clear()
        self._pages[slug] = (time.time(), data)
        return data

    def _lookup(self, slug: str) -> Optional[Dict[str, Any]]:
        for video in self.catalog():
            if video.get("slug") == slug:
                return video
        return None

    # ------------------------------------------------------------------ info

    def info(self, url: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Return everything publicly known about one video.

        Combines the catalog entry with the watch page (higher-resolution
        poster, storyboard, likes/dislikes, next episode). The result is a flat
        dict - v1 returned ``{"hentai_video": {...}}``.

        Raises:
            NotFound: the slug does not exist.
            NetworkError: the request failed.
        """
        slug = self.slug(url)
        data = self._page_data(slug)
        entry = self._lookup(slug)

        video: Dict[str, Any] = dict(entry) if entry else {}
        video["slug"] = slug
        video["url"] = "%s/videos/hentai/%s" % (BASE_URL, slug)

        if not _clean_text(video.get("name")):
            video["name"] = _clean_text(
                data.get("video_name") or data.get("og:title") or data.get("title")
            ) or slug

        if data.get("poster_url"):
            video["poster_url"] = data["poster_url"]
        elif not video.get("poster_url") and data.get("og_image"):
            video["poster_url"] = data["og_image"]

        if data.get("video_id") and not video.get("id"):
            video["id"] = _as_int(data.get("video_id"))

        for key in ("likes", "dislikes"):
            if data.get(key) is not None and video.get(key) is None:
                video[key] = _as_int(data.get(key))

        storyboard = _storyboard(data)
        if storyboard:
            video["storyboard"] = storyboard

        next_href = data.get("next_video_href")
        if isinstance(next_href, str) and next_href:
            video["next_video_url"] = urljoin(BASE_URL + "/", next_href)

        return video

    def storyboards(
        self, url: Union[str, Dict[str, Any]], *, image: bool = False
    ) -> Dict[str, Any]:
        """Return the storyboard sprite sheet for a video.

        Args:
            url: Video URL, catalog dict or slug.
            image: Also download the JPEG and include it as ``image`` bytes
                (plus ``content_type``).

        Returns:
            ``{"url", "columns", "rows", "tile_count"}``, each sprite being one
            frame thumbnail at ``(row, column)``.
        """
        slug = self.slug(url)
        data = self._page_data(slug)
        storyboard = _storyboard(data)
        if not storyboard:
            raise NotFound("no storyboard published for %r" % slug)

        if image:
            response = self._get(storyboard["url"])
            storyboard["image"] = response.content
            storyboard["content_type"] = response.headers.get("content-type")
        return storyboard

    # --------------------------------------------------------------- browser

    def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        *,
        headless: bool = False,
        profile_dir: Optional[str] = None,
        wait_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        """Sign in through a real browser window and keep the session on disk.

        hanime.tv signs every authenticated request inside its own WebAssembly
        module, so the sign-in has to happen in a browser. The profile is
        persistent: log in once, and :meth:`download` works from then on.

        Args:
            email: Optional; prefilled into the form. The security check itself
                is always left to you (keep the window visible).
            password: Optional; same.
            headless: Hide the window. Only useful once the profile exists.
            profile_dir: Where the browser profile lives
                (default ``~/.htv/browser``).
            wait_seconds: How long to wait for the sign-in to complete.

        Returns:
            The signed-in user object.
        """
        from .browser import BrowserSession

        with BrowserSession(profile_dir=profile_dir, headless=headless) as session:
            if session.is_logged_in():
                return session.user() or {}
            return session.login(email, password, wait_seconds=wait_seconds)

    def login_cookies(
        self,
        cookie_header: str,
        *,
        headless: bool = True,
        profile_dir: Optional[str] = None,
        domain: str = ".hanime.tv",
    ) -> Dict[str, Any]:
        """Adopt a session copied from a browser you signed into by hand.

        Cloudflare's Turnstile often refuses the automated browser outright
        (``code 600010``), but a session created in a normal browser works
        fine. Log in at hanime.tv in that browser, open DevTools -> Network ->
        any hanime.tv request -> Request Headers -> copy the ``cookie:`` value,
        and pass it here.

        Args:
            cookie_header: The raw ``Cookie`` header (``name=value; ...``).
            headless: Run without a window (the default; no challenge is shown).
            profile_dir: Browser profile to store the session in.
            domain: Cookie domain; the default covers all hanime.tv hosts.

        Returns:
            The signed-in user object.

        Raises:
            LoginRequired: The cookies did not produce a signed-in session.
        """
        from .browser import BrowserSession

        with BrowserSession(profile_dir=profile_dir, headless=headless) as session:
            session.import_cookies(cookie_header, domain=domain)
            user = session.user()
            if not user:
                raise LoginRequired(
                    "those cookies did not produce a signed-in session. Copy the "
                    "whole `cookie:` header from a request made *while signed in* "
                    "on hanime.tv, and make sure the session has not expired."
                )
            return user

    def download(
        self,
        url: Union[str, Dict[str, Any]],
        *,
        out_dir: Union[str, "os.PathLike[str]"] = ".",
        quality: Optional[str] = None,
        headless: bool = False,
        profile_dir: Optional[str] = None,
        resume: bool = True,
        progress: bool = True,
    ) -> "Path":
        """Download one video as MP4.

        Works without an account: hanime.tv serves free-tier (up to 720p) links
        to anonymous callers. A signed-in profile only matters for
        account-restricted content, which is why ``profile_dir`` exists.

        Args:
            url: Video URL, catalog dict or slug.
            out_dir: Destination directory (created if missing).
            quality: Preferred resolution, e.g. ``"1080"`` or ``"720"``. The
                closest available link is used when it is absent.
            headless: Run the browser without a window. The stored profile is
                already signed in, so this normally works.
            profile_dir: Browser profile to reuse.
            resume: Continue a partial ``.part`` file.
            progress: Show a progress line on stderr.

        Returns:
            Path of the downloaded file.

        Raises:
            LoginRequired: The video is account-restricted and the profile has no session.
            BrowserUnavailable: Playwright/Chromium is not installed.
            DownloadError: The transfer failed.
        """
        from .browser import BrowserSession
        from .download import download_links

        info = self.info(url)
        with BrowserSession(profile_dir=profile_dir, headless=headless) as session:
            result = session.download_links(info["slug"], info.get("id"))

        return download_links(
            result["links"],
            info.get("name") or info["slug"],
            out_dir,
            quality=quality,
            premium=result.get("is_premium_user", False),
            resume=resume,
            progress=progress,
        )

    def streams(
        self,
        url: Union[str, Dict[str, Any]],
        *,
        headless: bool = True,
        profile_dir: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the playable HLS sources of a video - no account required.

        Each entry has ``src`` (absolute URL), ``type``, ``label``, ``height``,
        ``width`` and ``kind`` (``"normal"`` is a real stream; ``"promotion"``
        entries are teasers and usually carry an empty ``src``).
        """
        from urllib.parse import urljoin

        from .browser import BrowserSession

        slug = self.slug(url)
        with BrowserSession(profile_dir=profile_dir, headless=headless) as session:
            sources = session.sources(slug)
        for source in sources:
            source["url"] = urljoin(BASE_URL + "/", source.get("src") or "")
        return sources

