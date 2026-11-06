"""Offline tests for the file downloader (local HTTP server, no internet)."""

import http.server
import threading

import pytest

from HTV.download import (
    download_file,
    download_links,
    filename_for,
    pick_link,
    safe_name,
)
from HTV.exceptions import DownloadError

PAYLOAD = bytes(range(256)) * 8192  # 2 MiB


class _Server:
    """Tiny local HTTP server with optional Range support and request log."""

    def __init__(self, payload=PAYLOAD, support_range=True):
        self.payload = payload
        self.support_range = support_range
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def do_GET(self):
                outer.requests.append((self.path, self.headers.get("Range")))
                if self.path.startswith("/missing"):
                    self.send_error(404)
                    return
                data = outer.payload
                start = 0
                range_header = self.headers.get("Range")
                if range_header and outer.support_range:
                    start = int(range_header.split("=", 1)[1].split("-")[0])
                    body = data[start:]
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
                else:
                    body = data
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def server():
    with _Server() as srv:
        yield srv


def test_download_file_writes_complete_file(server, tmp_path):
    target = tmp_path / "video.mp4"
    result = download_file(server.base + "/video.mp4", target, progress=False)
    assert result == target
    assert target.read_bytes() == PAYLOAD
    assert not (tmp_path / "video.mp4.part").exists()
    assert server.requests == [("/video.mp4", None)]


def test_download_file_resumes_partial(server, tmp_path):
    target = tmp_path / "video.mp4"
    part = tmp_path / "video.mp4.part"
    part.write_bytes(PAYLOAD[:500_000])

    download_file(server.base + "/video.mp4", target, progress=False)

    assert target.read_bytes() == PAYLOAD
    assert server.requests[0][1] == "bytes=500000-"


def test_download_file_restarts_when_server_ignores_range(tmp_path):
    with _Server(support_range=False) as server:
        target = tmp_path / "video.mp4"
        (tmp_path / "video.mp4.part").write_bytes(PAYLOAD[:100_000])
        download_file(server.base + "/video.mp4", target, progress=False)
        assert target.read_bytes() == PAYLOAD


def test_download_file_is_idempotent(server, tmp_path):
    target = tmp_path / "video.mp4"
    target.write_bytes(PAYLOAD)
    download_file(server.base + "/video.mp4", target, progress=False)
    assert server.requests == []


def test_download_file_reports_http_errors(server, tmp_path):
    with pytest.raises(DownloadError):
        download_file(server.base + "/missing.mp4", tmp_path / "x.mp4", progress=False)


def test_download_file_reports_network_errors(tmp_path):
    with pytest.raises(DownloadError):
        download_file("http://127.0.0.1:1/nope.mp4", tmp_path / "x.mp4", progress=False)


# ------------------------------------------------------------------ helpers


def test_pick_link_prefers_requested_quality():
    links = [
        {"url": "http://x/1080.mp4", "quality": "1080p"},
        {"url": "http://x/720.mp4", "quality": "720p"},
        {"url": "http://x/480.mp4", "quality": "480p"},
    ]
    assert pick_link(links, "720")["url"] == "http://x/720.mp4"
    assert pick_link(links)["url"] == "http://x/1080.mp4"


def test_pick_link_falls_back_when_quality_missing():
    links = [{"url": "http://x/480.mp4", "label": "480p"}]
    assert pick_link(links, "1080")["url"] == "http://x/480.mp4"


def test_pick_link_handles_empty_and_invalid():
    assert pick_link([]) is None
    assert pick_link([{"url": ""}]) is None
    assert pick_link([{"quality": "720p"}]) is None


def test_pick_link_reads_resolution_from_url():
    links = [{"url": "http://x/foo_1080p.mp4"}, {"url": "http://x/bar_720p.mp4"}]
    assert pick_link(links)["url"] == "http://x/foo_1080p.mp4"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Example Video", "Example Video"),
        ("a/b:c*d?e", "a b c d e"),
        ("  trailing.  ", "trailing"),
        ("", "video"),
        (None, "video"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_truncates():
    assert len(safe_name("x" * 400)) <= 120


def test_filename_for():
    assert filename_for("Example Video", 720, "http://x/a.mp4") == "Example Video [720p].mp4"
    assert filename_for("Thing", "1080p", "http://x/a.mkv") == "Thing [1080p].mkv"
    assert filename_for("Thing", "", "http://x/a") == "Thing.mp4"


def test_download_links_end_to_end(server, tmp_path):
    links = [
        {"url": server.base + "/a_1080p.mp4", "quality": "1080p"},
        {"url": server.base + "/a_720p.mp4", "quality": "720p"},
    ]
    path = download_links(links, "Example Video", tmp_path, quality="720", progress=False)
    assert path.name == "Example Video [720p].mp4"
    assert path.read_bytes() == PAYLOAD
    assert server.requests[0][0] == "/a_720p.mp4"


def test_direct_url_rewrites_pixeldrain_pages():
    from HTV.download import direct_url

    assert direct_url("https://pixeldrain.com/u/abc123") == "https://pixeldrain.com/api/file/abc123?download"
    assert direct_url("https://pixeldrain.net/u/abc123/") == "https://pixeldrain.com/api/file/abc123?download"
    assert direct_url("https://pixeldrain.com/api/file/abc123") == "https://pixeldrain.com/api/file/abc123"
    assert direct_url("https://example.com/a.mp4") == "https://example.com/a.mp4"


def test_download_links_without_links():
    with pytest.raises(DownloadError):
        download_links([], "x", ".", progress=False)
