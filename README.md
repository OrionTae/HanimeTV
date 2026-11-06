# HanimeTV

Python client and CLI for [hanime.tv](https://hanime.tv): catalog search, video
metadata, storyboards, playable stream URLs, and MP4 downloads.

> **v2.2** — the 2019 client is dead (`search.hanime.tv`, `members.hanime.tv` and
> `mapi.htvanime.com` no longer exist). Metadata works anonymously; sessions come
> from your own browser, because hanime.tv signs authenticated requests inside its
> WebAssembly module and blocks scripted logins.

## Installation

```sh
pip install -r requirements.txt        # metadata only: requests
pip install ".[browser]"               # + login and downloads (Playwright)
playwright install chromium            # one-time browser download
```

Python 3.8+.

## First run

Metadata needs nothing but `requests`:

```sh
python -m HTV search "demo" -n 5
python -m HTV info example-video
python -m HTV storyboards example-video
```

Downloads need the `browser` extra and **no account** — despite what the site's
UI suggests, free-tier links (up to 720p) are served to anonymous callers:

```sh
pip install ".[browser]" && playwright install chromium
python -m HTV download example-video -q 720
```

An account only matters for 1080p and other premium content. If you have a
premium plan, sign in once and the tool will pick up whatever your tier offers:

```sh
python -m HTV login                       # browser window (best-effort)
# or, when Cloudflare refuses that window:
python -m HTV login --cookies-file cookie.txt
```

| Step | Works with | Needs |
| --- | --- | --- |
| search / info / storyboards | ✅ | `requests` |
| streams | ✅ | `browser` extra |
| download (up to 720p) | ✅ | `browser` extra |
| download 1080p / premium | ✅ | `browser` extra + a session |

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
python -m HTV login --cookies-file cookie.txt   # or adopt a browser session
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

## Login (optional)

Free-tier downloads need no account — hanime.tv's UI hides its download button
behind a login, but the API hands the links to anonymous callers. A session is
only useful for premium content (1080p) and account features.

Getting one is the awkward part: `auth.hanime.tv` requires a Cloudflare
Turnstile challenge plus an `x-signature` / `x-time` header pair that only the
site's WebAssembly module can mint, and forging that signature is out of scope
here. So `login()` drives a real Chromium page: the site signs its own requests,
you complete the security check, and the session is stored in a persistent
profile (`~/.htv/browser` by default).

```python
hentai = HanimeTV()
hentai.login()                                    # once, browser window
path = hentai.download("example-video", quality="720", out_dir="videos")
print(path)                                       # videos/Example Video [720p].mp4
```

### If Cloudflare refuses the login window

Turnstile frequently scores an automated browser as a bot and answers
`code 600010` — that is not a bug you can fix from code, and this project does
not try to evade it. Sign in with a browser you already use, then lend the tool
that session:

1. Sign in — or create the account — at <https://hanime.tv> in your normal
   browser.
2. DevTools → **Network** → reload → click the `hanime.tv` document request →
   **Headers** → Request Headers → copy the value of `cookie:`.
3. Save it to `cookie.txt`.
4. Import it:

   ```sh
   python -m HTV login --cookies-file cookie.txt
   ```

   ```python
   hentai.login_cookies(open("cookie.txt").read())
   ```

5. Delete `cookie.txt`. The session now lives in the browser profile.

The cookies are only as good as the session they carry: when it expires,
repeat step 1. Turning a VPN back on afterwards should not invalidate the
session (Turnstile only guards the login), but if downloads start failing after
a network change, re-import before assuming something broke.

Downloads stream to `<name>.part` and resume on rerun, so an interrupted
transfer is not lost. hanime.tv hands out pixeldrain share pages; the tool
resolves them to the direct file API automatically.

Notes:

- `login(headless=True)` works only once the profile exists.
- Free accounts get 720p/480p/360p; 1080p is premium-only.
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
| `login_cookies(header, *, headless=True)` | adopt a session copied from your own browser |
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
- **Free tier stops at 720p.** 1080p needs a premium account; the tool picks
  whatever the account offers.
- One `BrowserSession` per thread at a time (Playwright's sync API).

## How it works

The reverse-engineered details — which hosts are signed, the CSRF pair, the
handshake token envelope (and why the player and the download panel return it
in different places), and the pixeldrain page/API split — are written up in
[docs/PROTOCOL.md](docs/PROTOCOL.md) so they don't have to be rediscovered.

## Tests

```sh
python -m pytest                      # 101 tests
python -m pytest tests/test_unit.py   # offline
HTV_BROWSER_TESTS=1 python -m pytest tests/test_browser.py   # launches Chromium
```

Offline suites cover slug parsing, ranking, filters, caching, the downloader
(local HTTP server, resume, errors) and the CLI. Live suites cover the catalog,
search, `info`, storyboards, and — when enabled — the browser handshake.

## Legal

MIT licensed. Unofficial client; respect the site's terms and the law where you
live. The service hosts adult content (18+).
