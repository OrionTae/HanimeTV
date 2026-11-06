"""Shared fixtures. Network-backed tests reuse one catalog download."""

import pytest

from HTV import HanimeTV

# A video that has been on the site for years and definitely has a storyboard.
KNOWN_SLUG = "example-video"


@pytest.fixture(scope="session")
def client():
    return HanimeTV()


@pytest.fixture(scope="session")
def catalog(client):
    return client.catalog()


@pytest.fixture(scope="session")
def sample(client, catalog):
    """A slug that exists right now, preferring a stable, well-known one."""
    slugs = {video["slug"] for video in catalog}
    if KNOWN_SLUG in slugs:
        return KNOWN_SLUG
    return catalog[0]["slug"]
