"""HanimeTV - a Python client for hanime.tv (public API + browser-backed downloads)."""

from .exceptions import (
    BrowserUnavailable,
    DownloadError,
    HanimeTVError,
    LoginRequired,
    NetworkError,
    NotFound,
    ParseError,
    UnsupportedFeature,
)
from .hanime import HanimeTV, __version__

__all__ = [
    "HanimeTV",
    "__version__",
    "HanimeTVError",
    "NetworkError",
    "NotFound",
    "ParseError",
    "UnsupportedFeature",
    "BrowserUnavailable",
    "LoginRequired",
    "DownloadError",
]
