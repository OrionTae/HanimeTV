"""Exceptions raised by :mod:`HTV`."""

__all__ = [
    "HanimeTVError",
    "NetworkError",
    "NotFound",
    "ParseError",
    "UnsupportedFeature",
]


class HanimeTVError(Exception):
    """Base class for every error raised by this library."""


class NetworkError(HanimeTVError):
    """The request could not be completed (DNS, TLS, timeout, connection reset...)."""


class NotFound(HanimeTVError, LookupError):
    """The requested video / resource does not exist on hanime.tv."""


class ParseError(HanimeTVError):
    """hanime.tv answered, but the response was not in the expected shape."""


class UnsupportedFeature(HanimeTVError):
    """The feature used to exist but is no longer reachable without a browser session."""
