"""Live tests: these hit hanime.tv. Run with ``pytest`` (network required)."""

import time

import pytest

from HTV import HanimeTV, NotFound

REQUIRED_KEYS = {
    "id",
    "name",
    "slug",
    "url",
    "brand",
    "tags",
    "views",
    "likes",
    "poster_url",
    "created_at",
}


def test_catalog_has_entries_with_expected_shape(catalog):
    assert len(catalog) > 1000
    assert REQUIRED_KEYS <= set(catalog[0])
    assert all(v["slug"] and v["name"] for v in catalog)


def test_catalog_urls_point_at_the_current_scheme(catalog):
    assert catalog[0]["url"].startswith("https://hanime.tv/videos/hentai/")


def test_search_returns_real_matches(client):
    results = client.search("example show", per_page=10)
    assert results
    assert any("demo" in v["slug"] for v in results)
    assert all(v["url"].endswith(v["slug"]) for v in results)


def test_search_pagination_does_not_repeat(client):
    page0 = client.search("", per_page=20, page=0, order="newest")
    page1 = client.search("", per_page=20, page=1, order="newest")
    assert len(page0) == len(page1) == 20
    assert not {v["slug"] for v in page0} & {v["slug"] for v in page1}


def test_search_by_tag(client):
    tags = client.tags()
    assert len(tags) > 20
    results = client.search("", tags=[tags[0]], per_page=5)
    assert all(tags[0] in v["tags"] for v in results)


def test_info_by_slug_matches_info_by_url(client, sample):
    by_slug = client.info(sample)
    by_url = client.info("https://hanime.tv/videos/hentai/" + sample)
    assert by_slug == by_url
    assert by_slug["slug"] == sample
    assert by_slug["name"]
    assert by_slug["poster_url"].startswith("https://")


def test_info_has_storyboard(client, sample):
    board = client.info(sample)["storyboard"]
    assert board["url"].startswith("https://")
    assert board["columns"] > 0 and board["rows"] > 0
    assert board["tile_count"] == board["columns"] * board["rows"]


def test_storyboards_can_be_downloaded(client, sample):
    board = client.storyboards(sample, image=True)
    assert board["content_type"].startswith("image/")
    assert board["image"][:3] == b"\xff\xd8\xff"  # JPEG magic
    assert len(board["image"]) > 10_000


def test_unknown_slug_raises_not_found(client):
    with pytest.raises(NotFound):
        client.info("this-slug-does-not-exist-0000")


def test_catalog_is_cached_in_memory(client):
    client.catalog()
    start = time.perf_counter()
    client.catalog()
    assert time.perf_counter() - start < 0.05


def test_refresh_bypasses_cache(client):
    assert len(client.refresh()) > 1000
