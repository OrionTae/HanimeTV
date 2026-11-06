# hanime.tv protocol notes (API v11)

How the site actually works, as verified against the live service in November
2026. Everything here was observed from a real browser session, not guessed —
the methods are at the bottom so each claim can be re-checked.

This is the reference for anyone changing `HTV/browser.py` or
`HTV/download.py`. User-facing docs live in the README.

## 1. Layers

| Host | Signed? | Provides |
| --- | --- | --- |
| `guest.freeanimehentai.net` | no | the whole public catalog |
| `hanime.tv` | no | watch pages (SSR), the site shell, HLS segments |
| `ct.hanime.tv` | no | CSRF tokens |
| `auth.hanime.tv` | **yes** | login, handshake, playlists, downloads |
| `hanime-cdn.com` | no | posters, storyboards, covers |

"Signed" means every request must carry `x-signature-version: web2`,
`x-signature` and `x-time` headers produced by the site's WebAssembly module.
Without them the API answers before it even parses the body:

```json
HTTP 401  {"error_id":"UA1","message":"Unauthorized"}
```

An empty JSON body gets the same answer, which is how you know it is a header
check and not validation. There is no public way to compute the signature.

## 2. Public catalog

```
GET https://guest.freeanimehentai.net/api/v11/search_hvs
→ {"data": [3463 videos], "ads": [...]}
```

- Query parameters are ignored (`?q=`, `?page=`, `?limit=` all return the same
  payload).
- `POST` to it returns `{"errors": ["Not found"]}`.
- The website therefore filters locally, and so does `HTV.search()`.

Video object: `id, name, slug, search_titles, description, brand, brand_id,
tags[], views, likes, dislikes, downloads, created_at(_unix),
released_at(_unix), cover_url, poster_url`. It has no storyboard and no
streams — those only exist on the watch page.

## 3. Watch page

```
GET https://hanime.tv/videos/hentai/<slug>
```

Astro server-renders the video, and the data sits in `<astro-island props="...">`
attributes as JSON with every value wrapped as `[0, value]`:

```
poster_url, storyboard_url, storyboard_columns, storyboard_rows,
storyboard_tile_count, video_id, video_name, likes, dislikes,
next_video_href, slug
```

This is where `info()` and `storyboards()` get their extras. Unknown slugs
return a real 404 page.

## 4. CSRF

```
GET https://ct.hanime.tv/csrf-token   (credentials: include)
→ {"csrf_token": "<uuid>|<signature>|<timestamp>", "csrf_token_expires_at": ...}
```

The response also sets an `htv_csrf_proof` cookie. The `x-csrf-token` header
must match that cookie:

- mismatched or stale → `HTTP 422 {"error_id":"CSRF_ERROR_4","message":"Invalid CSRF token"}`
- the token is cheap to mint, so `_HANDSHAKE_JS` **always** fetches a fresh pair
  immediately before the handshake. Reusing the page's token, or one carried in
  by an imported session, fails.

## 5. The handshake

Single endpoint used for both playback and downloads:

```
POST https://auth.hanime.tv/api/v11/handshake
headers: content-type, accept, x-signature-version: web2, x-signature,
         x-time, x-csrf-token        (credentials: include)
body:    {"token": "<opaque string>"}
```

### 5.1 Request token

Despite the name this is not a secret-key signature. The site's own code calls
it `htv-insecure-handshake-v1` and the key is a public constant compiled into
the bundle:

```
key  = SHA-256("htv-insecure-handshake-v1")          # 32 bytes
aad  = "htv-insecure-v1"
iv   = 12 random bytes
ct   = AES-256-GCM(key, iv, aad, JSON(payload))       # ciphertext || tag
env  = {"v":1, "alg":"AES-256-GCM",
        "iv": b64url(iv), "tag": b64url(ct[-16:]),
        "data": b64url(ct[:-16])}
token = b64url(JSON(env))                             # no padding
```

Note `tag` is the **last** 16 bytes and `data` the rest. Swapping them produces
`HTTP 422 {"error_id":"HANDSHAKE_ERROR_1","message":"Invalid token"}` — the most
likely mistake when editing this code.

Payload:

```json
{"timestamp_unix": 1793983773, "directive": "htv_player_handshake", "slug": "example-video"}
{"timestamp_unix": 1793983773, "directive": "get_download_links", "slug": "...", "hentai_video_id": 3600}
```

`timestamp_unix` is stamped at call time — the server rejects stale tokens.

### 5.2 Response token — two shapes

| Directive | Token arrives in | Notes |
| --- | --- | --- |
| `htv_player_handshake` | `x-token` **response header** | what the player reads |
| `get_download_links` | `{"status":"OK","message":"UPDATED","token":"..."}` **JSON body** | what the download panel reads |

Both are the same envelope as above; decrypt with the same key. Reading only
one location is why downloads can look like "no links returned" when they
actually succeeded. The client accepts either.

Decrypted contents:

```
player    → {sources: [...], is_preroll_enabled, preroll_urls, ...}
downloads → {download_links: [...], is_premium_user: bool}
```

Other directives seen in the bundle: `analytics`.

## 6. Login

```
POST https://auth.hanime.tv/api/v11/login
{"email", "password", "csrf_token", "turnstile_token"}
```

Needs the signed headers *and* a Cloudflare Turnstile token produced by the
widget on `/login` (`turnstile_public_site_key` in `window.AppConfig`).

Turnstile refuses the automated browser and VPN exit IPs alike:

```json
{"code": "600010", "message": "The security check failed (code 600010). Disable ad blockers or VPN..."}
```

Observed: `600010` with a Proton US exit **and** with a clean Azerbaijani
residential IP; it also persists after removing a spoofed Firefox UA from
Chromium (a UA that disagrees with `sec-ch-ua` is itself a signal). Treat the
login window as best-effort and prefer cookies (§8).

## 7. Sessions and cookies

A signed-in browser holds, on `.hanime.tv`:

| Cookie | Role |
| --- | --- |
| `htv_session` | the session itself (~100 chars) |
| `htv_csrf_proof` | pairs with `x-csrf-token` |
| `session_cookie_present` | marker the frontend reads |

`window.S.session.user` is **not** the source of truth: it is populated from
`localStorage.storeS`, which an imported cookie never populates. A profile can
therefore report `is_logged_in() == False` while the server happily serves
download links. Never gate a server call on it — attempt the call and map
401/403 to "not signed in".

## 8. Getting a session into the tool

Two paths:

1. **Login window** — `login()`. Blocked by Turnstile in practice (§6).
2. **Cookie import** — `login_cookies()` / `login --cookies-file`. Sign in with
   a normal browser, copy the `cookie:` request header from DevTools, import it.
   This is the path that works.

The cookies are installed into the Playwright profile (`~/.htv/browser`) with
`domain=".hanime.tv"`, `secure`, `httpOnly`, `sameSite=Lax`, and a 7-day expiry.
Switching a VPN back on afterwards is not expected to invalidate the session —
only Turnstile inspects the exit IP and the handshake does not — but that is
inference, not a measured result: if downloads start failing after a network
change, re-import the cookies before assuming the tool is broken.

## 9. Streams

`sources[]` entries:

```
{src: "/hls/3600/<token>/...", type: "application/x-mpegURL",
 label: "720p", height: 720, width: 1280, kind: "normal"}
```

`kind: "promotion"` entries are teaser slots with an empty `src` (often the
1080p row on a free account). Resolve `src` against `https://hanime.tv` to get
the manifest URL; segments are ordinary HLS.

## 10. Download links

```
download_links: [{label: "720p", height: 720, width: 854,
                  url: "https://pixeldrain.com/u/pJWxrHUz"}, ...]
```

The URL is a **share page**, not a file:

| URL | Returns |
| --- | --- |
| `https://pixeldrain.com/u/<id>` | `text/html` — the viewer page |
| `https://pixeldrain.net/u/<id>` | `text/html` — same site |
| `https://pixeldrain.com/api/file/<id>` | `video/mp4`, ~174 MB, supports Range |

So `download.direct_url()` rewrites `/u/<id>` to `/api/file/<id>?download`.
Downloading the share page would silently save an HTML file with an `.mp4`
extension.

Free accounts are offered 720p / 480p / 360p; 1080p is premium-only
(`is_premium_user: false`).

## 11. Error ids

| Response | Meaning |
| --- | --- |
| `401 {"error_id":"UA1"}` | missing/invalid `x-signature` (checked before the body) |
| `422 {"error_id":"CSRF_ERROR_4"}` | `x-csrf-token` does not match `htv_csrf_proof` |
| `422 {"error_id":"HANDSHAKE_ERROR_1"}` | request token could not be decrypted (envelope/tag order) |
| Turnstile `600010` | login refused: VPN, automation, or ad-blocker |

## 12. Re-verifying

```sh
# catalog + watch page (no browser needed)
python -m HTV info example-video

# streams (headless browser, anonymous)
python -m HTV streams example-video

# handshake + download links (needs a session)
python -m pytest tests/test_browser.py -q          # HTV_BROWSER_TESTS=1
python -m HTV login --cookies-file cookie.txt
python -m HTV download example-video -q 720 -o downloads
```

If the handshake starts failing, re-check `window.AppConfig` in the page HTML
first: it names the current `guest_api_base_url`, `search_hvs_url`,
`csrf_token_url` and Turnstile site key. A change there explains most breaks.
