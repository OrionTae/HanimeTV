"""HanimeTV - a Python client for the public hanime.tv API."""

from .exceptions import (
    HanimeTVError,
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
]
