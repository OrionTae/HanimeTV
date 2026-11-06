"""Offline tests: no network, everything is fed synthetic data."""

import pytest

from HTV import HanimeTV, LoginRequired, NotFound, UnsupportedFeature
from HTV.hanime import (
    ORDERINGS,
    _PageParser,
    _decode_props,
    _storyboard,
)

CATALOG = [
    {
        "id": 1,
        "name": "Example Show 1",
        "slug": "example-show-1",
        "search_titles": "Example Show One",
        "brand": "Queen Bee",
        "tags": ["incest", "big boobs"],
        "views": 300,
        "likes": 10,
        "created_at_unix": 100,
        "released_at_unix": 90,
    },
    {
        "id": 2,
        "name": "Example Show 2",
        "slug": "example-show-2",
        "search_titles": "",
        "brand": "Queen Bee",
        "tags": ["incest"],
        "views": 900,
        "likes": 50,
        "created_at_unix": 200,
        "released_at_unix": 190,
    },
    {
        "id": 3,
        "name": "Example Video",
        "slug": "example-video",
        "search_titles": "Example Video Six",
        "brand": "Suzuki Mirano",
        "tags": ["big boobs"],
        "views": 500,
        "likes": 30,
        "created_at_unix": 300,
        "released_at_unix": 290,
    },
]


@pytest.fixture
def offline(monkeypatch):
    """A client whose catalog is the fixture above and which never dials out."""
    h = HanimeTV()
    monkeypatch.setattr(h, "_catalog", [h._normalise(v) for v in CATALOG])
    monkeypatch.setattr(h, "_catalog_at", 10**12)  # never expires

    def boom(*args, **kwargs):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(h, "_get", boom)
    return h


# ------------------------------------------------------------------ slug()


@pytest.mark.parametrize(
    "value",
    [
        "example-video",
        "https://hanime.tv/videos/hentai/example-video",
        "https://hanime.tv/videos/hentai/example-video/",
        "https://hanime.tv/videos/hentai/example-video?x=1#top",
        "https://hanime.tv/hentai-videos/example-video",
        "/videos/hentai/example-video",
        "https://hanime.tv/videos/hentai/example-video".replace("https://", ""),
        {"slug": "example-video"},
        {"url": "https://hanime.tv/videos/hentai/example-video"},
    ],
)
def test_slug_accepts_every_form(value):
    assert HanimeTV.slug(value) == "example-video"


@pytest.mark.parametrize("value", ["", "   ", "/videos/hentai/", "https://hanime.tv/"])
def test_slug_rejects_empty(value):
    with pytest.raises(ValueError):
        HanimeTV.slug(value)


def test_slug_rejects_wrong_type():
    with pytest.raises(TypeError):
        HanimeTV.slug(42)


# ------------------------------------------------------------ props decoding


def test_decode_props_unwraps_astro_values():
    raw = '{"slug":[0,"abc"],"storyboard_columns":[0,5],"plain":true}'
    assert _decode_props(raw) == {"slug": "abc", "storyboard_columns": 5, "plain": True}


@pytest.mark.parametrize("raw", [None, "", "not json", "[1,2,3]", "null"])
def test_decode_props_tolerates_garbage(raw):
    assert _decode_props(raw) == {}


def test_storyboard_derives_tile_count():
    data = {
        "storyboard_url": "https://cdn/x.jpg",
        "storyboard_columns": 5,
        "storyboard_rows": 36,
    }
    assert _storyboard(data)["tile_count"] == 180


def test_storyboard_absent():
    assert _storyboard({}) is None
    assert _storyboard({"storyboard_url": ""}) is None


def test_page_parser_reads_islands_title_and_meta():
    html = """
    <html><head><title>Watch Thing</title>
    <meta property="og:image" content="https://cdn/p.jpg"></head>
    <body>
    <astro-island component-url="/a.js" props="{&quot;slug&quot;:[0,&quot;thing&quot;]}"></astro-island>
    <astro-island props="{&quot;storyboard_rows&quot;:[0,9]}"></astro-island>
    </body></html>
    """
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    assert parser.title == "Watch Thing"
    assert parser.meta["og:image"] == "https://cdn/p.jpg"
    assert len(parser.islands) == 2
    assert _decode_props(parser.islands[0]["props"]) == {"slug": "thing"}
    assert _decode_props(parser.islands[1]["props"]) == {"storyboard_rows": 9}


# ---------------------------------------------------------------- catalog()


def test_catalog_is_cached(offline, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return {"data": CATALOG}

    offline._catalog = None
    monkeypatch.setattr(offline, "_get", fake_get)
    offline.catalog()
    offline.catalog()
    offline.catalog(refresh=True)
    assert len(calls) == 2  # initial + explicit refresh


def test_catalog_adds_url(offline):
    assert offline.catalog()[0]["url"] == "https://hanime.tv/videos/hentai/example-show-1"


# ----------------------------------------------------------------- search()


def test_search_ranks_exact_name_first(offline):
    results = offline.search("example video")
    assert results[0]["slug"] == "example-video"


def test_search_matches_alternate_titles(offline):
    assert [v["slug"] for v in offline.search("one")] == ["example-show-1"]


def test_search_requires_every_token(offline):
    assert offline.search("demo sister") == []


def test_search_unknown_query_is_empty(offline):
    assert offline.search("zzzzz") == []


def test_search_empty_query_browses_by_views(offline):
    assert [v["slug"] for v in offline.search("")] == [
        "example-show-2",
        "example-video",
        "example-show-1",
    ]


@pytest.mark.parametrize(
    "order, expected",
    [
        ("newest", ["example-video", "example-show-2", "example-show-1"]),
        ("oldest", ["example-show-1", "example-show-2", "example-video"]),
        ("likes", ["example-show-2", "example-video", "example-show-1"]),
        ("name", ["example-show-1", "example-show-2", "example-video"]),
    ],
)
def test_search_ordering(offline, order, expected):
    assert [v["slug"] for v in offline.search("", order=order)] == expected


def test_search_random_keeps_everything(offline):
    assert sorted(v["slug"] for v in offline.search("", order="random")) == sorted(
        v["slug"] for v in CATALOG
    )


def test_search_filters_tags_and_brand(offline):
    assert [v["slug"] for v in offline.search("", tags=["big boobs"])] == [
        "example-video",
        "example-show-1",
    ]
    assert [v["slug"] for v in offline.search("", tags=["big boobs", "incest"])] == [
        "example-show-1"
    ]
    assert [v["slug"] for v in offline.search("", brand="suzuki mirano")] == [
        "example-video"
    ]


def test_search_pagination(offline):
    first = offline.search("", per_page=2, page=0, order="newest")
    second = offline.search("", per_page=2, page=1, order="newest")
    assert [v["slug"] for v in first] == ["example-video", "example-show-2"]
    assert [v["slug"] for v in second] == ["example-show-1"]
    assert len(offline.search("", per_page=2, all=True)) == 3
    assert len(offline.search("", per_page=None)) == 3


def test_search_validates_arguments(offline):
    with pytest.raises(ValueError):
        offline.search("x", order="nope")
    with pytest.raises(ValueError):
        offline.search("x", page=-1)
    with pytest.raises(ValueError):
        offline.search("x", per_page=0)


def test_tags_and_brands(offline):
    assert offline.tags() == ["big boobs", "incest"]
    assert offline.brands() == ["Queen Bee", "Suzuki Mirano"]


# ------------------------------------------------------- info() / storyboards()


def _fake_page(slug, **extra):
    data = {
        "slug": slug,
        "poster_url": "https://cdn/%s-poster.webp" % slug,
        "storyboard_url": "https://cdn/%s-storyboard.jpg" % slug,
        "storyboard_columns": 5,
        "storyboard_rows": 36,
        "storyboard_tile_count": 180,
        "video_id": 42,
        "likes": 7,
        "dislikes": 1,
        "next_video_href": "/videos/hentai/next-one",
        "video_name": "Example Video",
    }
    data.update(extra)
    return data


def test_info_merges_catalog_and_page(offline, monkeypatch):
    monkeypatch.setattr(offline, "_page_data", lambda slug: _fake_page(slug))
    info = offline.info("https://hanime.tv/videos/hentai/example-video")
    assert info["slug"] == "example-video"
    assert info["url"] == "https://hanime.tv/videos/hentai/example-video"
    assert info["name"] == "Example Video"
    assert info["brand"] == "Suzuki Mirano"
    assert info["poster_url"] == "https://cdn/example-video-poster.webp"
    assert info["storyboard"]["tile_count"] == 180
    assert info["next_video_url"] == "https://hanime.tv/videos/hentai/next-one"


def test_info_for_slug_outside_catalog(offline, monkeypatch):
    monkeypatch.setattr(
        offline, "_page_data", lambda slug: _fake_page(slug, video_name="Brand New")
    )
    info = offline.info("brand-new-thing")
    assert info["name"] == "Brand New"
    assert info["id"] == 42


def test_storyboards_metadata_offline(offline, monkeypatch):
    monkeypatch.setattr(offline, "_page_data", lambda slug: _fake_page(slug))
    board = offline.storyboards("example-video")
    assert board == {
        "url": "https://cdn/example-video-storyboard.jpg",
        "columns": 5,
        "rows": 36,
        "tile_count": 180,
    }


def test_storyboards_missing_raises_not_found(offline, monkeypatch):
    monkeypatch.setattr(offline, "_page_data", lambda slug: {"slug": slug})
    with pytest.raises(NotFound):
        offline.storyboards("example-video")


# ------------------------------------------------------------------- browser


class _FakeSession:
    """Stand-in for HTV.browser.BrowserSession (no Chromium in unit tests)."""

    def __init__(self, *, links=None, user=None, error=None, sources=None):
        self._links = links or []
        self._user = user
        self._error = error
        self._sources = sources or []
        self.profile_dir = "unused"
        self.headless = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def is_logged_in(self):
        return bool(self._user)

    def user(self):
        return self._user

    def login(self, email=None, password=None, wait_seconds=0):
        if self._error:
            raise self._error
        self._user = {"name": "tester"}
        return self._user

    def download_links(self, slug, video_id=None):
        if self._error:
            raise self._error
        return {"links": self._links, "is_premium_user": False}

    def sources(self, slug):
        return self._sources


def test_login_returns_user_from_browser(offline, monkeypatch):
    import HTV.browser as browser

    monkeypatch.setattr(browser, "BrowserSession", lambda **kw: _FakeSession(user={"name": "tester"}))
    assert offline.login()["name"] == "tester"


def test_download_propagates_login_required(offline, monkeypatch):
    import HTV.browser as browser

    monkeypatch.setattr(
        browser,
        "BrowserSession",
        lambda **kw: _FakeSession(error=LoginRequired("sign in first")),
    )
    monkeypatch.setattr(offline, "info", lambda url: {"slug": "example-video", "name": "Example Video"})
    with pytest.raises(LoginRequired):
        offline.download("example-video", progress=False)


def test_download_uses_links_from_browser(offline, monkeypatch, tmp_path):
    import HTV.browser as browser

    payload = b"x" * 4096
    link = {"url": "http://example.invalid/video.mp4", "quality": "720"}
    monkeypatch.setattr(browser, "BrowserSession", lambda **kw: _FakeSession(links=[link]))
    monkeypatch.setattr(offline, "info", lambda url: {"slug": "example-video", "name": "Example Video"})

    written = {}

    def fake_download(links, name, out_dir, **kwargs):
        written["links"] = links
        written["name"] = name
        written["quality"] = kwargs.get("quality")
        path = tmp_path / "out.mp4"
        path.write_bytes(payload)
        return path

    import HTV.download as download

    monkeypatch.setattr(download, "download_links", fake_download)
    result = offline.download("example-video", out_dir=tmp_path, quality="720")

    assert result.read_bytes() == payload
    assert written["links"] == [link]
    assert written["name"] == "Example Video"
    assert written["quality"] == "720"


def test_streams_builds_absolute_urls(offline, monkeypatch):
    import HTV.browser as browser

    monkeypatch.setattr(
        browser,
        "BrowserSession",
        lambda **kw: _FakeSession(sources=[{"src": "/hls/1/token/master.m3u8", "label": "720p"}]),
    )
    streams = offline.streams("example-video")
    assert streams[0]["url"] == "https://hanime.tv/hls/1/token/master.m3u8"


def test_credentials_are_rejected_at_construction():
    with pytest.raises(UnsupportedFeature):
        HanimeTV(email="a@b.c", password="hunter2")
    with pytest.raises(UnsupportedFeature):
        HanimeTV(email="a@b.c")


def test_orderings_are_documented():
    assert set(ORDERINGS) >= {"relevance", "newest", "views", "name"}
