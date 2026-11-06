"""Browser-backed access to the endpoints hanime.tv protects with its WASM signature.

Every request to ``auth.hanime.tv`` must carry ``x-signature`` / ``x-time``
headers that only the site's own WebAssembly module can produce. Rather than
reimplementing that check (which would mean forging an access-control token),
this module drives a real Chromium page: the site's own JavaScript signs the
request, and we only read the JSON it gets back.

One-time setup::

    pip install "hanimeTV[browser]"
    playwright install chromium

The login is stored in a persistent browser profile, so it survives restarts.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .exceptions import BrowserUnavailable, HanimeTVError, LoginRequired

__all__ = ["BrowserSession", "DEFAULT_PROFILE_DIR", "parse_cookie_header"]

DEFAULT_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".htv", "browser")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0"
)

# Executed inside the page. Builds the handshake token with the site's public
# scheme ("htv-insecure-handshake-v1"), lets the page's own WASM sign the HTTP
# request, then decrypts the response header. Nothing here is secret: the key
# is a constant string shipped in the site's own JavaScript.
_HANDSHAKE_JS = r"""
async ({slug, directive, videoId}) => {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const b64u = (u8) => { let s = ''; for (const b of u8) s += String.fromCharCode(b);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); };
  const unb64u = (s) => { const t = s.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(t + '='.repeat((4 - t.length % 4) % 4));
    const u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; };
  const concat = (a, b) => { const o = new Uint8Array(a.length + b.length);
    o.set(a, 0); o.set(b, a.length); return o; };

  const keyBytes = await crypto.subtle.digest('SHA-256', enc.encode('htv-insecure-handshake-v1'));
  const key = await crypto.subtle.importKey('raw', keyBytes, {name: 'AES-GCM'}, false, ['encrypt', 'decrypt']);
  const aad = enc.encode('htv-insecure-v1');

  // Always mint a fresh CSRF token: it must match the htv_csrf_proof cookie,
  // and an imported session may carry a stale pair.
  try {
    const c = await (await fetch('https://ct.hanime.tv/csrf-token',
      {credentials: 'include', cache: 'no-store'})).json();
    if (window.S && c?.csrf_token) window.S.csrf_token = c.csrf_token;
  } catch (e) { /* keep the page's token if the refresh fails */ }

  const payload = {timestamp_unix: Math.floor(Date.now() / 1000), directive, slug};
  if (videoId) payload.hentai_video_id = Number(videoId);

  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    {name: 'AES-GCM', iv, additionalData: aad, tagLength: 128}, key, enc.encode(JSON.stringify(payload))));
  const token = b64u(enc.encode(JSON.stringify({
    v: 1, alg: 'AES-256-GCM', iv: b64u(iv), tag: b64u(ct.slice(ct.length - 16)), data: b64u(ct.slice(0, ct.length - 16)),
  })));

  const response = await fetch('https://auth.hanime.tv/api/v11/handshake', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'content-type': 'application/json',
      'accept': 'application/json',
      'x-signature-version': 'web2',
      'x-signature': window.ssignature,
      'x-time': window.stime,
      'x-csrf-token': window.S?.csrf_token || '',
    },
    body: JSON.stringify({token}),
  });

  if (!response.ok) {
    return {ok: false, status: response.status, error: (await response.text()).slice(0, 400)};
  }
  // The player returns the payload in the x-token header; get_download_links
  // returns it as {"token": ...} in the JSON body. Accept either.
  let xt = response.headers.get('x-token');
  if (!xt) {
    let payload = null;
    try { payload = await response.json(); } catch (e) { /* not JSON */ }
    if (payload && typeof payload.token === 'string') xt = payload.token;
    else {
      return {ok: false, status: response.status,
              error: 'no token in response; body=' + JSON.stringify(payload).slice(0, 300)};
    }
  }

  const env = JSON.parse(dec.decode(unb64u(xt)));
  const plain = await crypto.subtle.decrypt(
    {name: 'AES-GCM', iv: unb64u(env.iv), additionalData: aad, tagLength: 128},
    key, concat(unb64u(env.data), unb64u(env.tag)));
  return {ok: true, status: response.status, data: JSON.parse(dec.decode(plain))};
}
"""

_SESSION_JS = """() => (window.S && window.S.session) || null"""

# Surfaces the site's own Cloudflare/Turnstile failure text, which is far more
# useful than waiting out the timeout (it usually says "disable VPN/adblock").
_LOGIN_MESSAGE_JS = r"""
() => {
  const nodes = Array.from(document.querySelectorAll('p, div, span, li'))
    .filter(n => n.children.length === 0);
  for (const node of nodes) {
    const text = (node.textContent || '').trim();
    if (text.length < 8 || text.length > 300) continue;
    if (/security check failed|disable ad blockers|disable.*vpn|verification failed/i.test(text)) {
      return text;
    }
  }
  return '';
}
"""


def parse_cookie_header(
    header: str, *, domain: str = ".hanime.tv", ttl_days: int = 7
) -> List[Dict[str, Any]]:
    """Turn a browser's ``Cookie:`` header into Playwright cookie dicts.

    Copy the value from DevTools (Network -> a hanime.tv request -> Request
    Headers -> cookie) after signing in normally. Accepts an optional leading
    ``cookie:`` label and surrounding quotes.
    """
    text = (header or "").strip()
    if not text:
        return []
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1]
    text = text.strip().strip('"').strip("'")

    expires = int(time.time()) + max(1, int(ttl_days)) * 86400
    cookies: List[Dict[str, Any]] = []
    seen = set()
    for part in text.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "expires": expires,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        )
    return cookies


class BrowserSession:
    """A persistent Chromium profile that talks to the signed endpoints.

    Use it as a context manager::

        with BrowserSession() as session:
            if not session.is_logged_in():
                session.import_cookies(cookie_header)
            links = session.download_links("some-slug", video_id=3600)

    Signing in through the window only works until Cloudflare's Turnstile
    decides the automated browser looks suspicious (it usually does, with
    code 600010). Importing cookies from a browser you logged into by hand
    avoids that entirely.
    """
    def __init__(
        self,
        *,
        profile_dir: Optional[str] = None,
        headless: bool = False,
        timeout: float = 90.0,
        user_agent: Optional[str] = None,
    ) -> None:
        self.profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self.headless = headless
        self.timeout = float(timeout)
        # None keeps Chromium's native UA. Forcing a Firefox UA here makes the
        # UA disagree with the sec-ch-ua client hints, which Cloudflare treats
        # as automation.
        self.user_agent = user_agent
        self._playwright = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------- lifecycle

    def __enter__(self) -> "BrowserSession":
        self.open()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def open(self) -> "BrowserSession":
        """Start Chromium with the persistent profile."""
        if self._context is not None:
            return self
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BrowserUnavailable(
                "playwright is not installed. Run:\n"
                '  pip install "hanimeTV[browser]"\n'
                "  playwright install chromium"
            ) from exc

        try:
            self._playwright = sync_playwright().start()
        except Exception as exc:
            raise BrowserUnavailable(
                "could not start the Playwright driver (%s). The sync API allows "
                "only one live session per thread - close the previous "
                "BrowserSession first." % exc
            ) from exc
        try:
            os.makedirs(self.profile_dir, exist_ok=True)
            options: Dict[str, Any] = {
                "headless": self.headless,
                "viewport": {"width": 1280, "height": 800},
                "accept_downloads": False,
            }
            if self.user_agent:
                options["user_agent"] = self.user_agent
            self._context = self._playwright.chromium.launch_persistent_context(
                self.profile_dir, **options
            )
        except Exception as exc:
            self._stop_playwright()
            raise BrowserUnavailable(
                "could not start Chromium (%s). If this is the first run, execute:\n"
                "  playwright install chromium" % exc
            ) from exc

        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.set_default_timeout(self.timeout * 1000)
        return self

    def close(self) -> None:
        """Close the browser and stop the Playwright driver."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        finally:
            self._context = None
            self._page = None
            self._stop_playwright()

    def _stop_playwright(self) -> None:
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "BrowserSession(profile_dir=%r, headless=%r)" % (self.profile_dir, self.headless)

    # ------------------------------------------------------------------ page

    @property
    def page(self):
        if self._page is None:
            raise HanimeTVError("browser session is not open; use it as a context manager")
        return self._page

    def _load_watch_page(self, slug: str) -> None:
        from urllib.parse import quote

        url = "https://hanime.tv/videos/hentai/" + quote(slug, safe="")
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        self._wait_for_signer()

    def _ensure_site_page(self) -> None:
        """Make sure the current tab is a hanime.tv page with its session object."""
        if self._page is None:
            self.open()
        if "hanime.tv" in (self._page.url or ""):
            return
        self.page.goto("https://hanime.tv/", wait_until="domcontentloaded", timeout=self.timeout * 1000)
        try:
            self.page.wait_for_function("() => Boolean(window.S)", timeout=self.timeout * 1000)
        except Exception as exc:  # pragma: no cover - timing dependent
            raise HanimeTVError("hanime.tv did not expose its session object in time") from exc

    def _wait_for_signer(self) -> None:
        """Wait until the page's WASM signer has published its headers."""
        try:
            self.page.wait_for_function(
                "() => Boolean(window.ssignature && window.S)", timeout=self.timeout * 1000
            )
        except Exception as exc:  # pragma: no cover - timing dependent
            raise HanimeTVError(
                "the hanime.tv page did not finish loading its security module; "
                "try again, or run with headless=False to watch the browser"
            ) from exc

    # --------------------------------------------------------------- session

    def session_state(self) -> Dict[str, Any]:
        """Return the site's session object (``user`` is ``None`` when logged out)."""
        self._ensure_site_page()
        return self.page.evaluate(_SESSION_JS) or {}

    def is_logged_in(self) -> bool:
        """True when the browser profile holds a signed-in account."""
        return bool((self.session_state() or {}).get("user"))

    def user(self) -> Optional[Dict[str, Any]]:
        """The signed-in user object, or ``None``."""
        return (self.session_state() or {}).get("user")

    def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        *,
        wait_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        """Open the login page and wait until the profile is signed in.

        With ``email``/``password`` the form is filled in; the Cloudflare
        Turnstile challenge is left to you (and to the real browser), so keep
        the window visible. Everything is stored in the profile directory.
        """
        self.open()
        self.page.goto("https://hanime.tv/login", wait_until="domcontentloaded", timeout=self.timeout * 1000)

        if email and password:
            try:
                self.page.fill("#Email", email)
                self.page.fill("#Password", password)
                self.page.press("#Password", "Enter")
            except Exception:
                # Fall back to manual entry rather than failing the whole login.
                pass

        deadline = time.time() + max(10.0, float(wait_seconds))
        while time.time() < deadline:
            try:
                state = self.page.evaluate(_SESSION_JS) or {}
                problem = self.page.evaluate(_LOGIN_MESSAGE_JS) or ""
            except Exception as exc:
                raise LoginRequired(
                    "the browser window was closed before the sign-in finished. "
                    "Run `login` again and leave that window open - it closes "
                    "itself as soon as the session is detected."
                ) from exc
            if state.get("user"):
                return state["user"]
            if problem:
                raise LoginRequired(
                    "hanime.tv rejected the sign-in: %r\n"
                    "Turnstile blocks VPN exit IPs, and it often blocks the "
                    "automated browser itself even on a clean connection. If "
                    "retrying without a VPN fails the same way, do this instead:\n"
                    "  1. sign in (or register) at https://hanime.tv in your "
                    "normal browser\n"
                    "  2. DevTools -> Network -> the hanime.tv request -> copy "
                    "the `cookie:` header\n"
                    "  3. python -m HTV login --cookies-file cookie.txt"
                    % problem
                )
            time.sleep(1.0)

        raise LoginRequired(
            "no signed-in session appeared within %d seconds. Run again with the "
            "browser visible, finish the login (including the security check), "
            "then retry." % int(wait_seconds)
        )

    def logout(self) -> None:
        """Forget the stored profile (closes the browser and deletes its directory)."""
        self.close()
        import shutil

        shutil.rmtree(self.profile_dir, ignore_errors=True)

    def import_cookies(
        self, cookie_header: str, *, domain: str = ".hanime.tv", ttl_days: int = 7
    ) -> int:
        """Load a session copied from a browser you signed into yourself.

        Returns the number of cookies installed. Raises :class:`HanimeTVError`
        when the header contains nothing usable.
        """
        cookies = parse_cookie_header(cookie_header, domain=domain, ttl_days=ttl_days)
        if not cookies:
            raise HanimeTVError("no cookies found in the supplied header")
        self.open()
        self._context.add_cookies(cookies)
        self.page.goto(
            "https://hanime.tv/", wait_until="domcontentloaded", timeout=self.timeout * 1000
        )
        try:
            self.page.wait_for_function(
                "() => Boolean(window.S)", timeout=self.timeout * 1000
            )
        except Exception:
            pass
        return len(cookies)

    # ------------------------------------------------------------- handshake

    def handshake(
        self,
        slug: str,
        directive: str = "htv_player_handshake",
        video_id: Optional[int] = None,
        *,
        retry: bool = True,
    ) -> Dict[str, Any]:
        """Ask the site's player endpoint for signed data about one video."""
        if self._page is None:
            self.open()
        self._load_watch_page(slug)

        result = self.page.evaluate(
            _HANDSHAKE_JS, {"slug": slug, "directive": directive, "videoId": video_id}
        )
        if not result.get("ok") and retry and result.get("status") in (401, 403, 419, 422):
            # A stale signature after a long idle period: reload and try once more.
            self.page.reload(wait_until="domcontentloaded")
            self._wait_for_signer()
            result = self.page.evaluate(
                _HANDSHAKE_JS, {"slug": slug, "directive": directive, "videoId": video_id}
            )

        if not result.get("ok"):
            raise HanimeTVError(
                "handshake for %r failed (HTTP %s): %s"
                % (slug, result.get("status"), result.get("error"))
            )
        return result.get("data") or {}

    def sources(self, slug: str) -> List[Dict[str, Any]]:
        """Playable HLS sources for a video (no account needed)."""
        data = self.handshake(slug, "htv_player_handshake")
        return [s for s in data.get("sources") or [] if s.get("src")]

    def download_links(self, slug: str, video_id: Optional[int] = None) -> Dict[str, Any]:
        """Ask for MP4 download links. Requires a valid session (cookie or login)."""
        try:
            data = self.handshake(slug, "get_download_links", video_id=video_id)
        except HanimeTVError as exc:
            text = str(exc)
            if any(marker in text for marker in ("401", "403", "Unauthorized", "login")):
                raise LoginRequired(
                    "hanime.tv refused the download links: the session is missing or "
                    "expired. Sign in with `login`, or re-import cookies with "
                    "`login --cookies-file`."
                ) from exc
            raise

        links = [link for link in data.get("download_links") or [] if link.get("url")]
        if not links:
            raise LoginRequired(
                "hanime.tv returned no download links for this video. That usually "
                "means the session is not signed in, or the account is not allowed "
                "to download it."
            )
        return {"links": links, "is_premium_user": bool(data.get("is_premium_user"))}
