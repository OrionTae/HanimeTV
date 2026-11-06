"""Live browser tests - opt-in, they launch Chromium and hit hanime.tv.

Enable with ``HTV_BROWSER_TESTS=1`` (plus ``playwright install chromium``).
"""

import os

import pytest

pytest.importorskip("playwright.sync_api")

from HTV import LoginRequired  # noqa: E402
from HTV.browser import BrowserSession  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not os.environ.get("HTV_BROWSER_TESTS"),
        reason="set HTV_BROWSER_TESTS=1 to run browser tests",
    ),
]

SLUG = "example-video"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    profile = tmp_path_factory.mktemp("profile")
    with BrowserSession(profile_dir=str(profile), headless=True) as s:
        yield s


def test_signer_is_available(session):
    session._load_watch_page(SLUG)
    signature = session.page.evaluate("window.ssignature")
    assert isinstance(signature, str) and len(signature) >= 32


def test_guest_has_no_user(session):
    assert session.is_logged_in() is False


def test_sources_returns_hls_streams(session):
    sources = session.sources(SLUG)
    assert sources, "expected at least one playable source"
    labels = {s.get("label") for s in sources}
    assert any(label in {"360p", "480p", "720p", "1080p"} for label in labels), labels
    assert all(s["src"].startswith("/hls/") for s in sources)
    assert all(s.get("type") == "application/x-mpegURL" for s in sources)


def test_download_links_requires_login(session):
    """A guest profile must be refused with a clear, catchable error."""
    assert session.is_logged_in() is False
    with pytest.raises(LoginRequired):
        session.download_links(SLUG)
