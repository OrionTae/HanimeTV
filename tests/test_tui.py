"""Interactive menu tests - no terminal, no network."""

import inspect
from pathlib import Path

import pytest

from HTV import HanimeTV
from HTV.tui import App, KeyReader, ask, select


# ----------------------------------------------------------------- selection


def test_select_accepts_a_number(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    assert select("Pick", ["a", "b", "c"]) == 1


def test_select_retries_on_bad_input(monkeypatch, capsys):
    answers = iter(["x", "9", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert select("Pick", ["a", "b"], default=1) == 1  # empty -> default
    assert "enter a number" in capsys.readouterr().out


def test_select_lists_options(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    select("Pick", ["alpha", "beta"])
    out = capsys.readouterr().out
    assert "1) alpha" in out and "2) beta" in out


def test_select_rejects_empty_options():
    with pytest.raises(ValueError):
        select("Pick", [])


def test_key_reader_is_inactive_without_tty():
    reader = KeyReader()
    assert reader.interactive is False
    assert reader.read() == "enter"


def test_ask_returns_default_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert ask("Query", default="demo") == "demo"


def test_ask_returns_typed_value(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "  sister  ")
    assert ask("Query") == "sister"


# ---------------------------------------------------------------------- app


def test_label_is_compact():
    text = App._label({"name": "A" * 90, "views": 1234567})
    assert "1,234,567 views" in text
    assert len(text) < 80


def test_files_lists_downloads(tmp_path, monkeypatch, capsys):
    (tmp_path / "one.mp4").write_bytes(b"x" * 2048)
    (tmp_path / "notes.txt").write_text("ignore me")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    App(out_dir=str(tmp_path)).files()

    out = capsys.readouterr().out
    assert "one.mp4" in out and "notes.txt" not in out


def test_files_handles_missing_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    App(out_dir=str(tmp_path / "nope")).files()
    assert "no downloads yet" in capsys.readouterr().out


def test_download_passes_quality_through(tmp_path, monkeypatch):
    calls = {}

    class FakeClient:
        def download(self, slug, **kwargs):
            calls["slug"] = slug
            calls.update(kwargs)
            return Path(tmp_path) / "x.mp4"

    answers = iter(["2"])  # 1080p
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("HTV.tui._pause", lambda: None)
    App(client=FakeClient(), out_dir=str(tmp_path)).download("example-video", "Sister")

    assert calls["slug"] == "example-video"
    assert calls["quality"] == "1080"
    assert calls["out_dir"] == str(tmp_path)
    assert calls["headless"] is True


def test_download_can_show_the_browser(tmp_path, monkeypatch):
    calls = {}

    class FakeClient:
        def download(self, slug, **kwargs):
            calls.update(kwargs)
            return Path(tmp_path) / "x.mp4"

    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    monkeypatch.setattr("HTV.tui._pause", lambda: None)
    App(client=FakeClient(), out_dir=str(tmp_path), headless=False).download("x", "X")
    assert calls["headless"] is False


def test_errors_do_not_crash_the_menu(monkeypatch, tmp_path, capsys):
    class Broken:
        def search(self, *args, **kwargs):
            raise RuntimeError("boom")

    answers = iter(["1", "x", "7"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("HTV.tui._pause", lambda: None)
    monkeypatch.setattr("HTV.tui._log_error", lambda exc: None)

    assert App(client=Broken(), out_dir=str(tmp_path)).run() == 0
    assert "boom" in capsys.readouterr().out


def test_best_quality_passes_none(tmp_path, monkeypatch):
    calls = {}

    class FakeClient:
        def download(self, slug, **kwargs):
            calls.update(kwargs)
            return Path(tmp_path) / "x.mp4"

    monkeypatch.setattr("builtins.input", lambda prompt="": "1")  # Best available
    monkeypatch.setattr("HTV.tui._pause", lambda: None)
    App(client=FakeClient(), out_dir=str(tmp_path)).download("x", "X")
    assert calls["quality"] is None


# ------------------------------------------------------------- defaults


def test_download_defaults_to_project_downloads_dir():
    signature = inspect.signature(HanimeTV.download)
    assert signature.parameters["out_dir"].default == "downloads"
