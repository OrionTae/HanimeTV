"""Shared fixtures. Network-backed tests reuse one catalog download.

``tmp_path`` is overridden because pytest's own temp root is unreliable inside
locked-down sandboxes (the session-finish cleanup can hit ``WinError 5``).
"""

import shutil
import uuid
from pathlib import Path

import pytest

from HTV import HanimeTV

_TMP_BASE = Path(__file__).resolve().parent / ".tmp"


@pytest.fixture(scope="session")
def tmp_path_factory():
    """Minimal stand-in for pytest's factory, rooted next to the tests."""
    _TMP_BASE.mkdir(parents=True, exist_ok=True)

    class Factory:
        def mktemp(self, basename="tmp", numbered=True):
            path = _TMP_BASE / ("%s-%s" % (basename, uuid.uuid4().hex[:8]))
            path.mkdir(parents=True, exist_ok=True)
            return path

        def getbasetemp(self):
            return _TMP_BASE

    yield Factory()
    shutil.rmtree(_TMP_BASE, ignore_errors=True)


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("case")


@pytest.fixture(scope="session")
def client():
    return HanimeTV()


@pytest.fixture(scope="session")
def catalog(client):
    return client.catalog()


@pytest.fixture(scope="session")
def sample(client, catalog):
    """A slug that exists right now - titles rotate, so never hardcode one."""
    return catalog[0]["slug"]
