# HanimeTV

Python client and CLI for [hanime.tv](https://hanime.tv): catalog search, video
metadata, storyboards, playable stream URLs, and MP4 downloads.

> **v2.1** — the 2019 client is dead (`search.hanime.tv`, `members.hanime.tv` and
> `mapi.htvanime.com` no longer exist). Metadata works anonymously; login and
> downloads go through a real browser, because hanime.tv signs those requests
> inside its own WebAssembly module.

## Installation

```sh
pip install -r requirements.txt        # metadata only: requests
pip install ".[browser]"               # + login and downloads (Playwright)
playwright install chromium            # one-time browser download
```

Python 3.8+.

## Quick start

```python
from HTV import HanimeTV

hentai = HanimeTV()

# SEARCH - matches titles, alternate titles and slugs
for video in hentai.search("Example Show", per_page=5):
    print(video["name"], "->", video["url"])

# GET INFO - URL, catalog dict or slug
info = hentai.info("example-video")
print(info["name"], info["brand"], info["views"])

# STORYBOARDS - sprite sheet metadata, optionally the JPEG itself
board = hentai.storyboards(info, image=True)
print(board["url"], board["columns"], board["rows"], len(board["image"]))
```

## Command line

```sh
python -m HTV login                       # opens a browser; sign in once
python -m HTV search "demo" -n 10
python -m HTV info example-video
python -m HTV streams example-video    # HLS URLs, no account needed
python -m HTV download example-video -q 720 -o ~/Videos
python -m HTV storyboards example-video --image board.jpg
python -m HTV whoami
python -m HTV logout
```

`pip install .` also provides the same commands as `htv`. `--json` is available
on `search`, `info` and `streams` for scripting.

## Login and downloads

hanime.tv protects `auth.hanime.tv` with two things a plain HTTP client cannot
produce: a Cloudflare Turnstile challenge on login, and an `x-signature` /
`x-time` header pair that only the site's WebAssembly module can mint. Forging
that signature is out of scope for this project.

So `login()` drives a real Chromium page instead: the site signs its own
requests, you complete the security check, and the resulting session is stored
in a persistent profile (`~/.htv/browser` by default). After that:

```python
hentai = HanimeTV()
hentai.login()                                    # once, browser window
path = hentai.download("example-video", quality="720", out_dir="videos")
print(path)                                       # videos/Example Video [720p].mp4
```

Downloads stream to `<name>.part` and resume on rerun, so an interrupted
transfer is not lost. The MP4 links themselves are ordinary file-host URLs.

Notes:

- `login(headless=True)` works only once the profile exists.
- The browser profile is a credential: treat `~/.htv/browser` like a password.
- `logout()` (or `python -m HTV logout`) deletes it.

## Library API

| Method | Returns |
| --- | --- |
| `catalog(refresh=False)` | every public video, cached for an hour |
| `refresh()` | force a catalog re-download |
| `search(query="", *, page=0, per_page=25, order="relevance", tags=None, brand=None, all=False)` | list of catalog dicts, best match first |
| `info(url)` | one video, merged with its watch page |
| `storyboards(url, image=False)` | `{"url", "columns", "rows", "tile_count"}` |
| `streams(url)` | playable HLS sources (no account) |
| `login(email=None, password=None, *, headless=False)` | user object; stores the browser profile |
| `download(url, *, out_dir=".", quality=None, headless=False)` | `Path` of the downloaded MP4 |
| `tags()`, `brands()` | sorted `list[str]` |
| `HanimeTV.slug(value)` | normalise URL / dict / slug |

`order` accepts `relevance`, `newest`, `oldest`, `released`, `views`, `likes`,
`downloads`, `name`, `random`.

Catalog items and `info()` results are plain dicts:

```
id, name, slug, url, brand, brand_id, description, tags, views, likes,
dislikes, downloads, search_titles, created_at, released_at,
poster_url, cover_url
```

`info()` adds `storyboard` and `next_video_url` when published.

Search is local: hanime.tv exposes one public catalog endpoint and filters it in
the browser, so there is no query API to call. The catalog is downloaded once
(~4.5 MB) and cached for an hour (`cache_ttl`).

## Errors

All inherit from `HTV.HanimeTVError`.

| Exception | Meaning |
| --- | --- |
| `NetworkError` | DNS/TLS/timeout/connection failure |
| `NotFound` | the slug does not exist |
| `ParseError` | response was not the expected shape |
| `BrowserUnavailable` | Playwright or Chromium is missing |
| `LoginRequired` | the operation needs a signed-in profile |
| `DownloadError` | a transfer failed or was left incomplete |
| `UnsupportedFeature` | base class for the three above |

## Limitations

- **No HLS ripping.** `streams()` returns the manifest URLs the player uses;
  assembling segments is left to tools like `yt-dlp` if you want it.
- **Downloads need an account**, because that is the site's own rule.
- One `BrowserSession` per thread at a time (Playwright's sync API).

## Tests

```sh
python -m pytest                      # 91 tests
python -m pytest tests/test_unit.py   # offline
HTV_BROWSER_TESTS=1 python -m pytest tests/test_browser.py   # launches Chromium
```

Offline suites cover slug parsing, ranking, filters, caching, the downloader
(local HTTP server, resume, errors) and the CLI. Live suites cover the catalog,
search, `info`, storyboards, and — when enabled — the browser handshake.

## Legal

MIT licensed. Unofficial client; respect the site's terms and the law where you
live. The service hosts adult content (18+).
