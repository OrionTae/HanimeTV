# HanimeTV

Python client for the public [hanime.tv](https://hanime.tv) API.

> **v2 — rewritten November 2026.** The 2019 client is dead: `search.hanime.tv`,
> `members.hanime.tv` and `mapi.htvanime.com` no longer exist. v2 talks to the
> API the site actually uses today and needs no account. See
> [what changed](#what-changed-in-v2).

## Installation

```sh
pip install -r requirements.txt   # requests only
# or
pip install .
```

Python 3.8+.

## Usage

```python
from HTV import HanimeTV

hentai = HanimeTV()

# SEARCH — matches titles, alternate titles and slugs
for video in hentai.search("Example Show", per_page=5):
    print(video["name"], "->", video["url"])

# BROWSE — an empty query lists the catalog
newest = hentai.search("", order="newest", per_page=10)
tagged = hentai.search("", tags=["big boobs"], brand="Suzuki Mirano")

# GET INFO — accepts a URL, a catalog dict, or a bare slug
info = hentai.info("https://hanime.tv/videos/hentai/example-show-1")
print(info["name"], info["brand"], info["views"], info["tags"])

# GET STORYBOARDS — sprite sheet metadata, optionally the JPEG itself
board = hentai.storyboards(info, image=True)
print(board["url"], board["columns"], board["rows"], len(board["image"]))
```

Because a search result carries its own `url`, results feed straight back in:

```python
for video in hentai.search("example video"):
    hentai.storyboards(video)
```

## API

| Method | Returns |
| --- | --- |
| `catalog(refresh=False)` | every public video (3463 at the time of writing), cached |
| `refresh()` | force a catalog re-download |
| `search(query="", page=0, per_page=25, order="relevance", tags=None, brand=None, all=False)` | list of catalog dicts, best match first |
| `info(url)` | one video, merged with its watch page |
| `storyboards(url, image=False)` | `{"url", "columns", "rows", "tile_count"}`, plus `{"image", "content_type"}` when `image=True` |
| `tags()` / `brands()` | sorted `list[str]` from the catalog |
| `HanimeTV.slug(value)` | normalise a URL / dict / slug to a slug |

`order` accepts `relevance`, `newest`, `oldest`, `released`, `views`, `likes`,
`downloads`, `name` and `random`.

Every catalog item and every `info()` result is a plain `dict`:

```
id, name, slug, url, brand, brand_id, description, tags, views, likes,
dislikes, downloads, search_titles, created_at, released_at,
poster_url, cover_url
```

`info()` adds `storyboard` (`{url, columns, rows, tile_count}`) and
`next_video_url` when the site publishes them.

Search is performed locally: hanime.tv exposes one public catalog endpoint and
filters it in the browser, so there is no server-side query API to call.
The catalog is downloaded once (~4.5 MB) and then cached for an hour
(`cache_ttl`), which is why the first `search()` takes a few seconds.

## Errors

All exceptions inherit from `HTV.HanimeTVError`.

| Exception | Meaning |
| --- | --- |
| `NetworkError` | DNS/TLS/timeout/connection failure |
| `NotFound` | the slug does not exist (HTTP 404) |
| `ParseError` | the response was not the shape we expect |
| `UnsupportedFeature` | login / downloads, which no longer work from a script |

## Not supported (and why)

- **`login()`** — sign-in requires a Cloudflare Turnstile challenge.
- **`download()`** — MP4 links are only issued to signed-in accounts, behind a
  handshake signed by the site's WebAssembly module.

Both raise `UnsupportedFeature` with an explanation instead of failing
mysteriously or silently returning junk. Open the video page in a browser for
those two. Streaming sources are gated the same way.

## What changed in v2

| | v1 (2019) | v2 |
| --- | --- | --- |
| Search | `POST search.hanime.tv` (host deleted) | local filter over the public catalog |
| Info | `members.hanime.tv/api/v5` (host deleted) | catalog + watch-page props |
| Storyboards | `members.hanime.tv/api/v1` (host deleted) | watch-page props |
| Login / download | `mapi.htvanime.com/api/v4` (host deleted) | `UnsupportedFeature` |
| Video URL | `/hentai-videos/<slug>` (404) | `/videos/hentai/<slug>` |
| Search / info return | raw server response | flat `dict` / `list[dict]` |

## Tests

```sh
python -m pytest            # 57 tests: unit + live
python -m pytest tests/test_unit.py   # offline only
```

`tests/test_unit.py` is fully offline (synthetic catalog, no sockets).
`tests/test_live.py` hits hanime.tv and therefore needs network access; it
verifies the catalog, search, pagination, `info()`, storyboard metadata and the
storyboard JPEG bytes.

## Legal

MIT licensed, as before. This is an unofficial client; respect the site's terms
and the law where you live. The service hosts adult content (18+).
