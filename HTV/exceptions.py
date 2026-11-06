"""Exceptions raised by :mod:`HTV`."""

__all__ = [
    "HanimeTVError",
    "NetworkError",
    "NotFound",
    "ParseError",
    "UnsupportedFeature",
    "BrowserUnavailable",
    "LoginRequired",
    "DownloadError",
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
    """The feature cannot be provided by this library (see the message)."""


class BrowserUnavailable(UnsupportedFeature):
    """Playwright (or its Chromium build) is missing, so signed requests cannot run."""


class LoginRequired(UnsupportedFeature):
    """The operation needs a signed-in hanime.tv account."""


class DownloadError(HanimeTVError):
    """A file could not be downloaded completely."""
