"""Live browser tests - opt-in, they launch Chromium and hit hanime.tv.

Enable with ``HTV_BROWSER_TESTS=1`` (plus ``playwright install chromium``).
"""

import os

import pytest

pytest.importorskip("playwright.sync_api")

from HTV.browser import BrowserSession  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not os.environ.get("HTV_BROWSER_TESTS"),
        reason="set HTV_BROWSER_TESTS=1 to run browser tests",
    ),
]

@pytest.fixture(scope="module")
def session(tmp_path_factory):
    profile = tmp_path_factory.mktemp("profile")
    with BrowserSession(profile_dir=str(profile), headless=True) as s:
        yield s


def test_signer_is_available(session, sample):
    session._load_watch_page(sample)
    signature = session.page.evaluate("window.ssignature")
    assert isinstance(signature, str) and len(signature) >= 32


def test_guest_has_no_user(session):
    assert session.is_logged_in() is False


def test_sources_returns_hls_streams(session, sample):
    sources = session.sources(sample)
    assert sources, "expected at least one playable source"
    labels = {s.get("label") for s in sources}
    assert any(label in {"360p", "480p", "720p", "1080p"} for label in labels), labels
    assert all(s["src"].startswith("/hls/") for s in sources)
    assert all(s.get("type") == "application/x-mpegURL" for s in sources)


def test_download_links_work_without_an_account(session, sample, catalog):
    """The API serves free-tier links to anonymous callers, unlike the UI."""
    assert session.is_logged_in() is False
    video_id = next(v["id"] for v in catalog if v["slug"] == sample)
    result = session.download_links(sample, video_id=video_id)
    assert result["links"], "expected anonymous download links"
    assert all(link["url"].startswith("https://pixeldrain") for link in result["links"])
    assert result["is_premium_user"] is False
