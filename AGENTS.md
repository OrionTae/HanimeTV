# AGENTS.md

Guidance for AI coding agents working in this repository. Read this before
changing code; it records facts about a third-party service that are expensive
to rediscover and easy to break.

## What this project is

`HTV` is a Python client and CLI for [hanime.tv](https://hanime.tv), an adult
anime streaming site.

- **Version:** 2.3.2 (`HTV/hanime.py`, `pyproject.toml` — keep both in sync).
- **Status:** working against the live site as of 2026-11.
- **Scope:** catalog search, per-video metadata, storyboards, playable HLS URLs,
  and MP4 downloads. Downloads need no account up to 720p; a session (login
  window or imported cookies) only unlocks premium content such as 1080p.
- **Audience:** the library talks to a site with adult content; that is expected,
  not a bug. Do not add content filtering, moralising messages, or age gates to
  library code — the README already states the 18+ nature.

## Commands

```sh
python -m pytest                      # 121 tests: 117 run + 4 opt-in browser
python -m pytest tests/test_unit.py   # offline, no sockets
python -m pytest tests/test_download.py tests/test_cli.py   # offline
HTV_BROWSER_TESTS=1 python -m pytest tests/test_browser.py  # launches Chromium
python example.py

python -m HTV                         # interactive menu (no subcommand)
python -m HTV search "demo" -n 5    # CLI
python -m HTV info <slug>
python -m HTV streams <slug>
python -m HTV login                   # browser window
python -m HTV login --cookies-file cookie.txt   # adopt a real-browser session
python -m HTV download <slug> -q 720   # -> downloads/
```

Runtime dependency: `requests`. Optional extra `browser` pulls `playwright`
(plus `playwright install chromium`).

## Repository map

| Path | Purpose |
| --- | --- |
| `HTV/hanime.py` | Metadata client: HTTP, caching, catalog, search, page parsing, `info`, `storyboards`, and the `login`/`download`/`streams` entry points. |
| `HTV/browser.py` | `BrowserSession`: Playwright profile, sign-in, and the in-page handshake that yields stream/download data. |
| `HTV/download.py` | Streaming file downloader: resume, progress, link selection, filename sanitising. |
| `HTV/cli.py`, `HTV/__main__.py` | `python -m HTV` / `htv` commands. |
| `HTV/tui.py` | Interactive menu (`python -m HTV`): arrow keys on a TTY, numbered prompts when piped. |
| `HTV/exceptions.py` | Exception hierarchy, rooted at `HanimeTVError`. |
| `tests/conftest.py` | Session fixtures **and** a `tmp_path` override (see Tests). |
| `tests/test_unit.py` | Offline client tests with a synthetic catalog. |
| `tests/test_download.py` | Offline downloader tests against a local HTTP server. |
| `tests/test_cli.py` | Offline parser/formatting tests. |
| `tests/test_live.py` | Metadata tests against the real site. |
| `tests/test_browser.py` | Opt-in Chromium tests (`HTV_BROWSER_TESTS=1`). |
| `docs/PROTOCOL.md` | The reverse-engineered v11 protocol: signed hosts, CSRF, token envelope, handshake response shapes, pixeldrain. Read it before touching the JS. |

## Architecture

Three data paths, two of them signed by the browser.

```
A. PUBLIC METADATA  (requests, no account)
   GET https://guest.freeanimehentai.net/api/v11/search_hvs
        -> {"data": [3463 videos], "ads": [...]}      catalog()  (cached 1 h)
        -> search() / tags() / brands() / _lookup()
   GET https://hanime.tv/videos/hentai/<slug>
        -> <astro-island props="..."> JSON            _page_data()  (cached 128)
        -> info() / storyboards()

B. STREAMS + DOWNLOAD LINKS  (Playwright, signed by the site's WASM)
   session   = login window OR imported cookies (login --cookies-file)
   page https://hanime.tv/videos/hentai/<slug>
        -> window.ssignature / window.stime exist
        -> refresh CSRF (ct.hanime.tv), build AES token
        -> POST https://auth.hanime.tv/api/v11/handshake
        -> player: x-token header  |  downloads: {"token": ...} in the body
        -> decrypt -> {"sources": [...]} or {"download_links": [...]}

C. FILE TRANSFER  (requests)
   download_links[i].url -> download_file() -> resume via .part -> final MP4
```

Facts that drive this design — verify before "simplifying" it:

1. **There is no server-side search.** The v11 catalog endpoint ignores query
   parameters and `POST` returns `{"errors": ["Not found"]}`; the site filters
   the full catalog in the browser. `search()` does the same locally.
2. **Metadata is unsigned.** `guest.freeanimehentai.net` needs no headers beyond
   a User-Agent, which is why `info()` and `search()` stay dependency-free.
3. **Everything under `auth.hanime.tv` is signed.** Requests carry
   `x-signature-version: web2`, `x-signature`, `x-time`, `x-csrf-token`; the
   signature is produced by the site's WebAssembly module. Without it even an
   empty POST returns `401 {"error_id":"UA1"}`. **Never forge it** — that is an
   access control, and the reason `BrowserSession` exists. Do not add stealth
   flags, UA spoofing tricks or captcha solving to make headless pass.
4. **The handshake payload crypto is not a secret.** The site itself ships
   `htv-insecure-handshake-v1` (AES-256-GCM, key = SHA-256 of that public
   string, AAD `htv-insecure-v1`, envelope `{v, alg, iv, tag, data}`, base64url,
   tag = last 16 bytes of the ciphertext). That is what decrypts `x-token`.
   Getting `data`/`tag` backwards yields `422 HANDSHAKE_ERROR_1` — the single
   most likely bug when touching `_HANDSHAKE_JS`.
5. **Sources are HLS, downloads are MP4.** `sources[]` entries have
   `src`, `type`, `label`, `height`, `width`, `kind`; `kind: "promotion"` is a
   teaser with an empty `src`, `kind: "normal"` is real. `download_links[]`
   entries have `label`/`height`/`width` and a **pixeldrain share page**, not a
   file. Two shapes, two endpoints — `/u/<id>` → `/api/file/<id>`,
   `/d/<id>` → `/api/filesystem/<id>`; mixing them gives `not_found`.
   `download.direct_url()` does the mapping, and `download_file()` refuses
   `text/*` / `application/json` responses so a page can never be saved as an
   `.mp4` again.
6. **CSRF must be re-minted per handshake.** `x-csrf-token` has to match the
   `htv_csrf_proof` cookie; a token cached from page load (or one baked into an
   imported session) fails with `CSRF_ERROR_4`. `_HANDSHAKE_JS` always fetches a
   fresh pair from `ct.hanime.tv` first.
7. **The client's login flag lies.** `window.S.session.user` comes from
   `localStorage.storeS`, which an imported cookie does not populate — so
   `is_logged_in()` can be `False` while the server happily accepts the session.
   Never gate a server call on it; attempt the call and translate a 401/403 (or
   an empty link list) into `LoginRequired`.
8. **Downloads do not require an account.** The site's UI hides the button
   behind `isLoggedIn`, but the API returns free-tier links to anonymous
   callers — verified with an empty profile: 3 links, `is_premium_user: false`.
   Do not put a login pre-check back in `download_links()`; a session only
   changes *which* qualities come back.

## Public API contract

Metadata returns plain, JSON-serialisable `dict` / `list[dict]`; do not
introduce dataclasses there — `json.dumps(client.search(...))` must keep working.

| Method | Notes |
| --- | --- |
| `HanimeTV(email=None, password=None, *, timeout=30.0, cache_ttl=3600.0, retries=2, session=None)` | Credentials still raise `UnsupportedFeature`; use `login()`. |
| `catalog(refresh=False)` | Full list; shallow copy (the dicts inside are shared). |
| `search(query="", *, page=0, per_page=25, order="relevance", tags=None, brand=None, all=False)` | Local filter; `per_page=None`/`all=True` returns everything. |
| `info(url)` | Always fetches the watch page, so unknown-but-valid slugs still work. |
| `storyboards(url, image=False)` | Adds `image` bytes + `content_type`. |
| `streams(url, *, headless=True)` | Browser path; rewrites `src` to an absolute `url`. |
| `login(email=None, password=None, *, headless=False, wait_seconds=300)` | Returns the user object; profile persists. Usually blocked by Turnstile. |
| `login_cookies(header, *, headless=True)` | Adopts a session copied from a real browser; the reliable path. |
| `download(url, *, out_dir="downloads", quality=None, headless=True, resume=True, progress=True)` | Returns `Path`; default folder is `downloads/` next to the CWD. Headless by default — a visible window can be closed mid-handshake and raise `TargetClosedError`. |
| `tags()`, `brands()`, `HanimeTV.slug(value)` | As before. |

`order` keywords live in `ORDERINGS`; `relevance` uses `_match()` tiers — exact
name 1000, name prefix 900, name substring 800, slug substring 700,
`search_titles` 600 — with `views` as the tie-breaker.

## Data shapes

```
id, name, slug, url, brand, brand_id, description, tags[], views, likes,
dislikes, downloads, search_titles, created_at, released_at,
poster_url, cover_url
```

`info()` adds `storyboard` (`{url, columns, rows, tile_count}`) and
`next_video_url`. `id` is the small internal video id (needed for
`get_download_links`), not a UUID.

## Invariants and gotchas

- **Never reintroduce the v1 hosts.** `search.hanime.tv`, `members.hanime.tv`,
  `mapi.htvanime.com` are NXDOMAIN; `api/v4`/`api/v5` are retired.
- **Video URLs are `/videos/hentai/<slug>`.** `slug()` also accepts the 2019
  `/hentai-videos/` form, and must keep rejecting non-video URLs
  (`https://hanime.tv/`, `/videos/hentai/`) instead of inventing a slug.
- **Do not send `x-signature` yourself.** Only the page can, via
  `window.ssignature`; a stale or fabricated value produces `401 UA1`.
- **One live `BrowserSession` per thread.** Playwright's sync API raises
  "inside the asyncio loop" otherwise; `open()` converts that into
  `BrowserUnavailable`. Reuse a session rather than nesting two.
- **Handshakes expire.** `_HANDSHAKE_JS` stamps `timestamp_unix` at call time and
  `handshake()` reloads once on 401/403/419/422.
- **The profile is a credential.** `~/.htv/browser` holds the session;
  `logout()` deletes it. Never log its contents or commit it.
- **Keying by `id()` is a trap.** Search scores are keyed by `slug`.
- **Python 3.8 floor.** `from __future__ import annotations` is on; keep runtime
  constructs 3.8-compatible (no `match`, no `X | Y` at runtime).
- **Typed errors only.** Wrap `requests` failures in `NetworkError`, 404 in
  `NotFound`, bad payloads in `ParseError`, transfers in `DownloadError`.
  Never let a raw `requests`/Playwright exception escape.

## Tests

- Offline tests must not touch the network: inject `_catalog`, monkeypatch
  `_get` to raise, and fake the browser with a `BrowserSession` stand-in.
- `tests/conftest.py` overrides `tmp_path`/`tmp_path_factory` to use
  `tests/.tmp`. Pytest's default temp root and `--basetemp` both hit
  `WinError 5` inside locked-down sandboxes; do not remove the override and do
  not re-add `--basetemp`.
- `pytest` cache is disabled (`-p no:cacheprovider`) for the same reason.
- Browser tests are opt-in (`HTV_BROWSER_TESTS=1`) because they download nothing
  but do launch Chromium and hit the site. Keep them few.
- Live tests reuse one catalog download via session fixtures. Do not hardcode
  freshly released slugs — they rotate; use the `sample` fixture.
- Live/browser tests talk to a third party: no loops, no crawling.

## When the site changes (the expected failure)

v1 died because a host disappeared. If tests fail with `NetworkError`, 404s, or
`422 HANDSHAKE_ERROR_1`, probe in this order:

```sh
python - <<'PY'
import json, urllib.request, requests
# 1. Catalog still there?
r = requests.get("https://guest.freeanimehentai.net/api/v11/search_hvs", timeout=60)
print(r.status_code, len(r.content), len(r.json().get("data", [])))
# 2. Watch page still Astro islands?
p = requests.get("https://hanime.tv/videos/hentai/<slug>", timeout=60)
print(p.status_code, "astro-island" in p.text, "storyboard_url" in p.text)
# 3. Domain still points at the same CDN?
q = "https://cloudflare-dns.com/dns-query?name=hanime.tv&type=A"
req = urllib.request.Request(q, headers={"accept": "application/dns-json"})
print(json.loads(urllib.request.urlopen(req, timeout=20).read())["Status"])
PY
```

Then read `window.AppConfig` in the page HTML — it names the current
`guest_api_base_url`, `search_hvs_url`, `csrf_token_url` and
`turnstile_public_site_key`. If the handshake breaks, capture a real one:

```sh
HTV_BROWSER_TESTS=1 python -m pytest tests/test_browser.py -q
```

and compare its decrypted payload with `_HANDSHAKE_JS`. Update constants, then
the JS, then the tests.

## Making changes

- New metadata method: read from `catalog()` or `_page_data()`, return a plain
  dict, add an offline test plus a live one.
- New browser-backed call: add a directive to `BrowserSession.handshake()` and
  a thin wrapper on `HanimeTV`; cover the no-login path with `LoginRequired`.
- Downloader changes: extend `tests/test_download.py` against the local server
  (it supports Range), never against the real file hosts.
- Docs: update `README.md` for user-facing changes, this file for architecture
  or invariants.
- Release: bump `__version__` **and** `pyproject.toml`, run `python example.py`,
  commit, tag `vX.Y.Z`.

## Don'ts

- Don't forge `x-signature`, solve Turnstile, add stealth flags, or otherwise
  defeat the site's bot protection. The browser path exists precisely so we
  don't have to.
- Don't add an HLS segment downloader; `streams()` returning URLs is the limit.
- Don't store credentials anywhere — no config file, no env var, no log line.
  The browser profile is the only state.
- Don't add a dependency when `requests` plus the stdlib suffices.
- Don't turn `LoginRequired`/`BrowserUnavailable` into silent fallbacks; they
  tell the user exactly which step is missing.
