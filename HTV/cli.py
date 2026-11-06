"""Command line interface: ``python -m HTV ...`` (or ``htv ...`` when installed)."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any, List, Optional, Sequence

from . import __version__
from .exceptions import HanimeTVError
from .hanime import HanimeTV

__all__ = ["main", "build_parser"]


def _client(args: argparse.Namespace) -> HanimeTV:
    return HanimeTV(timeout=args.timeout)


def _print_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _table(rows: List[Sequence[str]], headers: Sequence[str]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


# ---------------------------------------------------------------- commands


def cmd_login(args: argparse.Namespace) -> int:
    if args.cookies or args.cookies_file:
        header = args.cookies or ""
        if args.cookies_file:
            from pathlib import Path

            try:
                header = Path(args.cookies_file).read_text(encoding="utf-8")
            except OSError as exc:
                print("error: cannot read %s: %s" % (args.cookies_file, exc), file=sys.stderr)
                return 2
        if not header.strip():
            print("error: empty cookie header", file=sys.stderr)
            return 2
        user = _client(args).login_cookies(header, profile_dir=args.profile)
        print("signed in as %s" % (user.get("name") or user.get("email") or "unknown user"))
        return 0

    password = args.password
    if args.email and password is None:
        password = getpass.getpass("hanime.tv password for %s: " % args.email)

    user = _client(args).login(
        email=args.email,
        password=password,
        headless=args.headless,
        profile_dir=args.profile,
        wait_seconds=args.wait,
    )
    print("signed in as %s" % (user.get("name") or user.get("email") or "unknown user"))
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    from .browser import BrowserSession

    BrowserSession(profile_dir=args.profile).logout()
    print("browser profile removed; you are signed out")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    from .browser import BrowserSession

    with BrowserSession(profile_dir=args.profile, headless=True) as session:
        user = session.user()
    if not user:
        print("not signed in (run `login`)")
        return 1
    print(json.dumps(user, indent=2, ensure_ascii=False))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    results = _client(args).search(
        args.query,
        page=args.page,
        per_page=args.limit,
        order=args.order,
        tags=args.tags.split(",") if args.tags else None,
        brand=args.brand,
    )
    if args.json:
        _print_json(results)
        return 0
    _table(
        [[v["name"][:48], v["slug"][:44], str(v.get("views", 0))] for v in results],
        ["NAME", "SLUG", "VIEWS"],
    )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    info = _client(args).info(args.video)
    if args.json:
        _print_json(info)
        return 0
    for key in ("name", "slug", "brand", "views", "likes", "released_at", "url"):
        if info.get(key) is not None:
            print("%-12s %s" % (key, info[key]))
    if info.get("tags"):
        print("%-12s %s" % ("tags", ", ".join(info["tags"])))
    if info.get("storyboard"):
        print("%-12s %s" % ("storyboard", info["storyboard"]["url"]))
    return 0


def cmd_streams(args: argparse.Namespace) -> int:
    streams = _client(args).streams(args.video, headless=not args.visible, profile_dir=args.profile)
    if args.json:
        _print_json(streams)
        return 0
    if not streams:
        print("no playable sources")
        return 1
    _table(
        [[str(s.get("label", "?")), str(s.get("height", "?")), s["url"]] for s in streams],
        ["QUALITY", "HEIGHT", "URL"],
    )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    path = _client(args).download(
        args.video,
        out_dir=args.out,
        quality=args.quality,
        headless=not args.visible,
        profile_dir=args.profile,
    )
    print(path)
    return 0


def cmd_storyboards(args: argparse.Namespace) -> int:
    board = _client(args).storyboards(args.video, image=bool(args.image))
    if args.image:
        with open(args.image, "wb") as handle:
            handle.write(board["image"])
        print("%s (%d bytes, %sx%s)" % (args.image, len(board["image"]), board["columns"], board["rows"]))
        return 0
    print(json.dumps({k: board[k] for k in ("url", "columns", "rows", "tile_count")}, indent=2))
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="htv", description="Browse hanime.tv and download videos."
    )
    parser.add_argument("--version", action="version", version="hanimeTV " + __version__)
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout (metadata)")
    parser.add_argument("--profile", default=None, help="browser profile directory")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="sign in through a browser window")
    p.add_argument("--email", default=None)
    p.add_argument("--password", default=None, help="prefer the interactive prompt")
    p.add_argument("--headless", action="store_true", help="hide the window (not for first login)")
    p.add_argument("--wait", type=float, default=300.0, help="seconds to wait for the sign-in")
    p.add_argument(
        "--cookies-file",
        default=None,
        help="adopt a session: file containing the `cookie:` header copied from your browser",
    )
    p.add_argument("--cookies", default=None, help="same as --cookies-file, inline")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="delete the stored browser profile")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("whoami", help="show the signed-in account")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("search", help="search the catalog")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--order", default="relevance", help="relevance|newest|views|likes|name|random")
    p.add_argument("--tags", default=None, help="comma separated, all must match")
    p.add_argument("--brand", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("info", help="show metadata for one video")
    p.add_argument("video", help="slug or URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("streams", help="list playable HLS sources (no account)")
    p.add_argument("video")
    p.add_argument("--visible", action="store_true", help="show the browser window")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_streams)

    p = sub.add_parser("download", help="download an MP4 (needs `login` first)")
    p.add_argument("video", help="slug or URL")
    p.add_argument("-o", "--out", default=".", help="output directory")
    p.add_argument("-q", "--quality", default=None, help="1080, 720, 480, 360")
    p.add_argument("--visible", action="store_true", help="show the browser window")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("storyboards", help="storyboard sprite sheet")
    p.add_argument("video")
    p.add_argument("--image", default=None, help="also save the JPEG here")
    p.set_defaults(func=cmd_storyboards)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HanimeTVError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
