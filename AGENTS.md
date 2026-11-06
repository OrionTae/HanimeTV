# AGENTS.md

Guidance for AI coding agents working in this repository. Read this before
changing code; it records facts about a third-party service that are expensive
to rediscover and easy to break.

## What this project is

`HTV` is a small Python client for the **public metadata** of
[hanime.tv](https://hanime.tv), an adult anime streaming site.

- **Version:** 2.0.0 (`HTV/hanime.py`, `pyproject.toml` — keep both in sync).
- **Status:** working against the live site as of 2026-11.
- **Scope:** catalog search, per-video metadata, storyboard sprite sheets.
  Nothing that requires an account.
- **Audience:** the library talks to a site with adult content; that is expected,
  not a bug. Do not add content filtering, moralising messages, or age gates to
  library code — the README already states the 18+ nature.

## Commands

```sh
python -m pytest                      # 57 tests: offline unit + live integration
python -m pytest tests/test_unit.py   # offline only, no sockets, fast
python -m pytest tests/test_live.py   # hits hanime.tv, needs network
python example.py                     # end-to-end smoke of the public API
```

There is no build step, no linter config, and no CI. `requirements.txt` and
`pyproject.toml` both list `requests` as the only runtime dependency.

## Repository map

| Path | Purpose |
| --- | --- |
| `HTV/hanime.py` | The whole client: HTTP, caching, catalog, search, page parsing, `info`, `storyboards`. ~500 lines. |
| `HTV/exceptions.py` | Exception hierarchy, all rooted at `HanimeTVError`. |
| `HTV/__init__.py` | Re-exports `HanimeTV`, `__version__` and the exceptions. |
| `tests/conftest.py` | Session-scoped `client`, `catalog`, `sample` fixtures (one catalog download for the whole live run). |
| `tests/test_unit.py` | Offline tests; a synthetic 3-video catalog is injected, `_get` is monkeypatched to fail loudly. |
| `tests/test_live.py` | Network tests against the real site. |
| `example.py` | Runnable usage example; keep it working. |
| `README.md` | User-facing docs, including the v1→v2 migration table. |

## Architecture

Two public sources, no authentication, no API key, no signing:

```
                 GET https://guest.freeanimehentai.net/api/v11/search_hvs
                 (4.5 MB JSON: {"data": [3463 video objects], "ads": [...]})
                                        |
                                        v
   catalog()  --->  self._catalog  (in memory, cache_ttl = 1 h)
     |                   |
     |                   +--> search()      local filter + rank + sort + paginate
     |                   +--> tags(), brands(), _lookup(slug) for info()
     |
     +--> search() returns list[dict], each with a ready-to-use video "url"


                 GET https://hanime.tv/videos/hentai/<slug>
                 (server-rendered HTML; data lives in <astro-island props="...">)
                                        |
                                        v
   _page_data(slug)  --->  merged props dict  (cached per slug, max 128, 1 h)
                                        |
                    +-------------------+-------------------+
                    v                                       v
                 info()                              storyboards(url, image=True)
        catalog entry + page extras              storyboard url/grid, optional JPEG
```

Facts that drive this design — verify before "simplifying" it:

1. **There is no server-side search.** The v11 catalog endpoint ignores every
   query parameter (`?q=`, `?search=`, `?page=` all return the identical full
   payload) and `POST` to it answers `{"errors": ["Not found"]}`. The website
   downloads the whole catalog and filters in the browser. `search()` does the
   same locally. This is why the first call takes ~5 s and everything after is
   instant.
2. **Video extras live in the HTML.** The watch page is Astro SSR; `poster_url`,
   `storyboard_url`, `storyboard_columns/rows/tile_count`, `video_id`,
   `likes`/`dislikes` and `next_video_href` are serialised into
   `<astro-island props="...">` attributes and merged by `_page_data()`.
3. **Astro props are `[0, value]` pairs.** Every value is wrapped, e.g.
   `"slug": [0, "foo"]`; `_decode_props()` unwraps them and tolerates garbage
   (ad islands are irrelevant to us and must never raise).
4. **Streaming and downloads cannot be implemented here.** Playback sources
   require a `token` from a WASM signer plus `POST /api/v11/handshake`, and MP4
   links additionally require a signed-in account. There is no public path.
   `login()` and `download()` raise `UnsupportedFeature` by design.

## Public API contract

Everything returns plain, JSON-serialisable `dict` / `list[dict]`. Do not
introduce dataclasses or custom objects — v1 users index results with `[...]`,
and `json.dumps(client.search(...))` must keep working.

| Method | Notes |
| --- | --- |
| `HanimeTV(email=None, password=None, *, timeout=30.0, cache_ttl=3600.0, retries=2, session=None)` | `email`/`password` accepted only to raise `UnsupportedFeature` with an explanation. |
| `catalog(refresh=False)` | Full list; shallow copy of the cached list (the dicts inside are shared — do not mutate them). |
| `search(query="", *, page=0, per_page=25, order="relevance", tags=None, brand=None, all=False)` | Local filter. `per_page=None` or `all=True` returns everything. |
| `info(url)` | URL, slug, or catalog dict. Always fetches the watch page, so it also works for a slug missing from the catalog. |
| `storyboards(url, image=False)` | `{"url", "columns", "rows", "tile_count"}`; adds `image` bytes + `content_type`. |
| `tags()`, `brands()` | Sorted unique values derived from the catalog. |
| `HanimeTV.slug(value)` | The single normaliser for URL → slug. |
| `login()`, `download()` | Always raise `UnsupportedFeature`. |

`order` keywords live in the module-level `ORDERINGS` dict: `relevance`,
`newest`, `oldest`, `released`, `views`, `likes`, `downloads`, `name`,
`random`. `relevance` uses `_match()` tiers — exact name 1000, name prefix 900,
name substring 800, slug substring 700, elsewhere (`search_titles`) 600 — and
breaks ties on `views`.

## Data shapes

Catalog item (and the base of every `info()` result):

```
id, name, slug, url, brand, brand_id, description, tags[], views, likes,
dislikes, downloads, search_titles, created_at, released_at,
poster_url, cover_url
```

`info()` additionally sets `storyboard` (`{url, columns, rows, tile_count}`)
and `next_video_url` when the page publishes them, and prefers the watch page's
higher-resolution `poster_url`. `id` is the hanime internal video id (small
int), not a UUID.

## Invariants and gotchas

- **Never reintroduce the v1 hosts.** `search.hanime.tv`, `members.hanime.tv`
  and `mapi.htvanime.com` are NXDOMAIN; `api/v4`/`api/v5` are retired. If you
  find them in a diff, the diff is wrong.
- **Video URLs are `/videos/hentai/<slug>`.** The 2019 `/hentai-videos/<slug>`
  scheme 404s. `slug()` accepts both plus bare slugs, and rejects
  non-video URLs (`https://hanime.tv/`, `/videos/hentai/`) instead of inventing
  a slug — keep that strictness, it is covered by tests.
- **Never send signature/session headers.** `x-signature`, `x-claim`,
  `x-session-token` belonged to a 2019 anti-bot scheme; replaying stale values
  only produces confusing errors. The public endpoints need a User-Agent and
  nothing else.
- **Keying by `id()` is a trap.** Search scores are keyed by `slug`: catalog
  dicts are rebuilt on refresh and `id()` values are not stable across calls.
- **Python 3.8 floor.** `from __future__ import annotations` is on, but keep
  runtime constructs 3.8-compatible (no `match`, no `X | Y` at runtime).
- **Caching semantics.** `catalog()` is memoised for `cache_ttl`; `refresh()`
  forces a re-download; `_page_data()` caches per slug (cap 128, cleared
  wholesale when exceeded). Tests rely on the memoisation — `test_catalog_is_cached`
  asserts `_get` is called once.
- **Errors are typed.** Wrap `requests` failures in `NetworkError`, HTTP 404 in
  `NotFound`, malformed payloads in `ParseError`. Never let a raw
  `requests` exception escape: that was the v1 failure mode.
- **Keep the retry policy narrow.** Retries apply to idempotent GETs only
  (`allowed_methods={"GET"}`, statuses 429/5xx).

## Tests

- Offline tests must never touch the network: inject `_catalog` and monkeypatch
  `_get` to raise `AssertionError` on any call. Follow the `offline` fixture.
- Live tests use session-scoped fixtures, so the 4.5 MB catalog is downloaded
  once per run. Add new live assertions against the `client` fixture, not by
  constructing `HanimeTV()` inline.
- Live tests pick their video from `sample`, which resolves to a stable slug
  present in the catalog — do not hardcode a newly released slug, they rotate.
- `pytest` cache is disabled in `pyproject.toml` (`-p no:cacheprovider`)
  because sandboxed runs leave unreadable temp directories behind. Do not
  re-enable it.
- Live tests spend real requests on a third-party site. Do not run them in a
  loop, and do not add tests that page through the whole catalog.

## When the site changes (the expected failure)

v1 died because a host disappeared. If tests start failing with `NetworkError`
or 404s, run this probe (POSIX heredoc; on Windows save the body as `probe.py`
and run `python probe.py`):

```sh
python - <<'PY'
import json, urllib.request, requests
# 1. Is the catalog still where we think it is?
r = requests.get("https://guest.freeanimehentai.net/api/v11/search_hvs", timeout=60)
print(r.status_code, r.headers.get("content-type"), len(r.content))
print(list(r.json())[:5], len(r.json()["data"]))

# 2. Is the watch-page markup still Astro islands?
p = requests.get("https://hanime.tv/videos/hentai/example-video", timeout=60)
print(p.status_code, 'astro-island' in p.text, 'storyboard_url' in p.text)

# 3. Has the site moved again? (authoritative, bypasses local DNS)
q = "https://cloudflare-dns.com/dns-query?name=hanime.tv&type=A"
req = urllib.request.Request(q, headers={"accept": "application/dns-json"})
print(json.loads(urllib.request.urlopen(req, timeout=20).read())["Status"])
PY
```

Then look for `window.AppConfig` in the page HTML — it names the current
`guest_api_base_url`, `search_hvs_url` and `csrf_token_url`. Update the module
constants, then the parser, then the tests, in that order.

## Making changes

- New metadata method: read it from `catalog()` or `_page_data()`, return a plain
  dict, add one offline test (fixture-driven) and one live test. Do not add new
  hosts without a verified endpoint.
- New field from the watch page: add it in `info()` next to the existing
  `poster_url` / `storyboard` merges, and cover it in
  `test_info_merges_catalog_and_page`.
- Bug fix: add the failing case to `tests/test_unit.py` first; it must fail
  offline, not just live.
- Docs: update `README.md` when the public API or its limits change; this file
  when architecture or invariants change.
- Release: bump `__version__` **and** `pyproject.toml`, commit, tag `vX.Y.Z`.
  `example.py` must run before tagging.

## Don'ts

- Don't add scraping of stream URLs, DRM circumvention, captcha solvers, or
  credential handling — out of scope and unmaintainable.
- Don't add a dependency when `requests` plus the stdlib (`html.parser`, `json`,
  `re`) suffices. `ujson` was dropped in v2.
- Don't cache aggressively at module import time; `HanimeTV()` must stay cheap
  and offline-safe.
- Don't remove `UnsupportedFeature` from `login()`/`download()` or turn them
  into silent no-ops; failing loudly is the feature.
