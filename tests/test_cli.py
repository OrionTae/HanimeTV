"""CLI tests: argument parsing and output formatting, no network."""

import json

import pytest

from HTV import HanimeTVError
from HTV import cli


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def search(self, query, **kwargs):
        return [
            {"name": "Example Show", "slug": "example-show-1", "views": 1234,
             "url": "https://hanime.tv/videos/hentai/example-show-1"},
        ]

    def info(self, video):
        return {"name": "Example Video", "slug": "example-video", "brand": "X",
                "views": 1, "url": "https://hanime.tv/videos/hentai/example-video",
                "tags": ["a", "b"]}


def test_parser_search():
    args = cli.build_parser().parse_args(["search", "demo", "-n", "5", "--order", "newest"])
    assert args.command == "search"
    assert args.query == "demo"
    assert args.limit == 5
    assert args.order == "newest"


def test_parser_download_defaults():
    args = cli.build_parser().parse_args(["download", "abc"])
    assert args.out == "downloads"
    assert args.quality is None
    assert args.visible is False


def test_parser_ui_defaults():
    args = cli.build_parser().parse_args(["ui"])
    assert args.command == "ui"
    assert args.out == "downloads"
    assert args.visible is False


def test_parser_allows_no_command_for_the_menu():
    args = cli.build_parser().parse_args([])
    assert args.command is None


def test_search_table(monkeypatch, capsys):
    monkeypatch.setattr(cli, "HanimeTV", _FakeClient)
    assert cli.main(["search", "demo"]) == 0
    out = capsys.readouterr().out
    assert "NAME" in out and "Example Show" in out and "1234" in out


def test_search_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "HanimeTV", _FakeClient)
    assert cli.main(["search", "demo", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "example-show-1"


def test_info_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "HanimeTV", _FakeClient)
    assert cli.main(["info", "example-video"]) == 0
    out = capsys.readouterr().out
    assert "Example Video" in out
    assert "tags" in out


def test_errors_are_reported_without_traceback(monkeypatch, capsys):
    class Boom(_FakeClient):
        def search(self, *a, **k):
            raise HanimeTVError("nope")

    monkeypatch.setattr(cli, "HanimeTV", Boom)
    assert cli.main(["search", "x"]) == 2
    assert "error: nope" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "hanimeTV" in capsys.readouterr().out
