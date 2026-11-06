"""Interactive terminal UI: ``python -m HTV`` (or ``python -m HTV ui``).

Arrow keys on a real terminal, numbered prompts when input is piped, and no
third-party dependency. Everything it does is a thin wrapper over the same
client calls the CLI exposes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .exceptions import HanimeTVError
from .hanime import HanimeTV

__all__ = ["run", "select", "ask", "KeyReader"]

QUALITY_CHOICES = ["Best available", "1080p", "720p", "480p", "360p"]


# --------------------------------------------------------------------- input


class KeyReader:
    """Read one keypress, returning ``up``/``down``/``enter``/``quit`` or the char."""

    def __init__(self) -> None:
        self.interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        self._windows = os.name == "nt"
        self._saved: Any = None

    def __enter__(self) -> "KeyReader":
        if self.interactive and not self._windows:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        elif self._windows:
            os.system("")  # enable ANSI escapes in the Windows console
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def read(self) -> str:
        if not self.interactive:
            return "enter"
        if self._windows:
            import msvcrt

            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(
                    msvcrt.getwch(), ""
                )
            if char in ("\r", "\n"):
                return "enter"
            if char == "\x1b":
                return "quit"
            return char.lower()

        char = sys.stdin.read(1)
        if char in ("\r", "\n"):
            return "enter"
        if char == "\x1b":
            rest = sys.stdin.read(2)
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(rest, "quit")
        if char == "\x03":
            raise KeyboardInterrupt
        return char.lower()


def _render(prompt: str, options: Sequence[str], index: int) -> None:
    lines = ["", prompt]
    for position, option in enumerate(options):
        marker = ">" if position == index else " "
        lines.append("  %s %s" % (marker, option))
    lines.append("  (up/down + enter, or press a number)")
    sys.stdout.write("\x1b[?25l" + "\n".join(lines) + "\n" + "\x1b[?25h")
    sys.stdout.flush()


def _clear(lines: int) -> None:
    sys.stdout.write("\x1b[%dA" % lines + "\x1b[J")
    sys.stdout.flush()


def select(
    prompt: str,
    options: Sequence[str],
    *,
    default: int = 0,
    reader: Optional[KeyReader] = None,
) -> int:
    """Pick one of ``options``; returns the index (0-based)."""
    if not options:
        raise ValueError("nothing to choose from")
    own_reader = reader is None
    reader = reader or KeyReader()

    if own_reader:
        reader.__enter__()
    try:
        if not reader.interactive:
            print(prompt)
            for position, option in enumerate(options, 1):
                print("  %d) %s" % (position, option))
            while True:
                try:
                    raw = input("choice [%d]: " % (default + 1)).strip() or str(default + 1)
                except EOFError:
                    raise KeyboardInterrupt
                if raw.isdigit() and 1 <= int(raw) <= len(options):
                    return int(raw) - 1
                print("  enter a number between 1 and %d" % len(options))

        index = default
        _render(prompt, options, index)
        while True:
            key = reader.read()
            if key == "up":
                index = (index - 1) % len(options)
            elif key == "down":
                index = (index + 1) % len(options)
            elif key == "enter":
                _clear(len(options) + 4)
                return index
            elif key == "quit":
                _clear(len(options) + 4)
                raise KeyboardInterrupt
            elif key.isdigit() and 1 <= int(key) <= len(options):
                index = int(key) - 1
                _clear(len(options) + 4)
                return index
            elif key in ("j", "k"):
                index = (index + (1 if key == "j" else -1)) % len(options)
            _clear(len(options) + 4)
            _render(prompt, options, index)
    finally:
        if own_reader:
            reader.__exit__(None, None, None)


def ask(prompt: str, default: str = "") -> str:
    """Single-line input with an optional default."""
    suffix = " [%s]" % default if default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        raise KeyboardInterrupt
    return value or default


def _pause() -> None:
    try:
        input("\npress enter to continue")
    except EOFError:
        raise KeyboardInterrupt


# ----------------------------------------------------------------------- app


class App:
    """Menu loop. Kept as a class so tests can drive it without a terminal."""

    def __init__(self, client: Optional[HanimeTV] = None, out_dir: str = "downloads") -> None:
        self.client = client or HanimeTV()
        self.out_dir = out_dir

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _label(video: Dict[str, Any], width: int = 52) -> str:
        name = str(video.get("name") or video.get("slug"))
        return "%s  (%s views)" % (name[:width].ljust(width), format(int(video.get("views") or 0), ","))

    def _pick_video(self, videos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not videos:
            print("\n  nothing found")
            _pause()
            return None
        try:
            index = select("Results", [self._label(v) for v in videos])
        except KeyboardInterrupt:
            return None
        return videos[index]

    # -- actions ---------------------------------------------------------

    def search(self) -> None:
        query = ask("Search")
        if not query:
            return
        print("\n  searching...")
        videos = self.client.search(query, per_page=25)
        video = self._pick_video(videos)
        if video:
            self.video_menu(video)

    def browse(self) -> None:
        order = ["newest", "views", "likes"]
        try:
            index = select("Sort by", ["Newest", "Most viewed", "Most liked"])
        except KeyboardInterrupt:
            return
        print("\n  loading...")
        videos = self.client.search("", order=order[index], per_page=25)
        video = self._pick_video(videos)
        if video:
            self.video_menu(video)

    def by_tag(self) -> None:
        tag = ask("Tag (e.g. big boobs)")
        if not tag:
            return
        print("\n  searching...")
        videos = self.client.search("", tags=[tag], per_page=25)
        video = self._pick_video(videos)
        if video:
            self.video_menu(video)

    def download_by_slug(self) -> None:
        target = ask("Video slug or URL")
        if not target:
            return
        self.video_menu({"slug": HanimeTV.slug(target), "name": HanimeTV.slug(target)})

    def video_menu(self, video: Dict[str, Any]) -> None:
        slug = video.get("slug") or HanimeTV.slug(video)
        print("\n  %s" % (video.get("name") or slug))
        for key in ("brand", "views", "released_at", "url"):
            if video.get(key):
                print("  %-12s %s" % (key, video[key]))
        try:
            while True:
                action = select(
                    "What now?",
                    ["Download", "Details", "Back"],
                    default=0,
                )
                if action == 0:
                    self.download(slug, video.get("name") or slug)
                    return
                if action == 1:
                    self.details(slug)
                else:
                    return
        except KeyboardInterrupt:
            return

    def details(self, slug: str) -> None:
        try:
            info = self.client.info(slug)
        except HanimeTVError as exc:
            print("\n  %s" % exc)
            _pause()
            return
        print()
        for key in ("name", "brand", "views", "likes", "released_at"):
            if info.get(key) is not None:
                print("  %-12s %s" % (key, info[key]))
        if info.get("tags"):
            print("  %-12s %s" % ("tags", ", ".join(info["tags"])))
        if info.get("storyboard"):
            print("  %-12s %s" % ("storyboard", info["storyboard"]["url"]))
        _pause()

    def download(self, slug: str, name: str) -> None:
        try:
            choice = select("Quality", QUALITY_CHOICES)
        except KeyboardInterrupt:
            return
        quality = None if choice == 0 else QUALITY_CHOICES[choice].rstrip("p")
        print("\n  fetching links and downloading to %s ...\n" % Path(self.out_dir).resolve())
        try:
            path = self.client.download(slug, out_dir=self.out_dir, quality=quality)
        except HanimeTVError as exc:
            print("\n  %s" % exc)
            _pause()
            return
        print("\n  saved: %s" % path)
        _pause()

    def files(self) -> None:
        directory = Path(self.out_dir)
        files = sorted(
            (p for p in directory.glob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm"}),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if directory.is_dir() else []
        if not files:
            print("\n  no downloads yet in %s" % directory.resolve())
            _pause()
            return
        print("\n  downloaded files in %s:\n" % directory.resolve())
        for item in files:
            print("  %8.1f MB  %s" % (item.stat().st_size / 1048576.0, item.name))
        _pause()

    def login(self) -> None:
        print("\n  a browser window will open; sign in there.")
        try:
            user = self.client.login()
        except HanimeTVError as exc:
            print("\n  %s" % exc)
            _pause()
            return
        print("\n  signed in as %s" % (user.get("name") or user.get("email") or "user"))
        _pause()

    # -- loop ------------------------------------------------------------

    ACTIONS = [
        ("Search", "search"),
        ("Browse", "browse"),
        ("Browse by tag", "by_tag"),
        ("Download by slug / URL", "download_by_slug"),
        ("Downloaded files", "files"),
        ("Login (optional, premium)", "login"),
        ("Quit", None),
    ]

    def run(self) -> int:
        while True:
            try:
                choice = select(
                    "hanime.tv - downloads go to %s" % Path(self.out_dir).resolve(),
                    [label for label, _ in self.ACTIONS],
                )
            except KeyboardInterrupt:
                print()
                return 0
            handler = self.ACTIONS[choice][1]
            if handler is None:
                return 0
            try:
                getattr(self, handler)()
            except KeyboardInterrupt:
                print()
            except HanimeTVError as exc:
                print("\n  %s" % exc)
                _pause()


def run(out_dir: str = "downloads") -> int:
    """Entry point for the interactive menu."""
    app = App(out_dir=out_dir)
    try:
        return app.run()
    except KeyboardInterrupt:
        print()
        return 0
