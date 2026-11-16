"""Offline evidence commands and explicitly selected passive capture."""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .evidence import read_capture
from .reports import compare, inventory, markdown, report


def decode_file(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility API for phase 1 callers."""
    capture = read_capture(path)
    return capture["records"], capture["errors"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NMEA 2000 capture evidence (offline by default)")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("decode", "inventory", "report"):
        subparser = commands.add_parser(command, help=f"{command} a local candump file")
        subparser.add_argument("log", type=Path)
        if command == "report":
            subparser.add_argument("--format", choices=("markdown", "json"), default="json")
    diff = commands.add_parser(
        "diff", help="compare offline observations without inferring causality"
    )
    diff.add_argument("baseline", type=Path)
    diff.add_argument("stimulus", type=Path)
    live = commands.add_parser("capture", help="explicit passive SocketCAN capture")
    live.add_argument("--channel", required=True)
    live.add_argument("--listen-only", action="store_true", required=True)
    live.add_argument(
        "--isolated-bench",
        action="store_true",
        help="confirm an isolated bench with verified termination (physical CAN)",
    )
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--duration", type=float, default=60)
    live.add_argument("--count", type=int)
    args = parser.parse_args(argv)
    if args.command == "capture":
        from .live import LOGGER, capture

        handler = logging.StreamHandler(sys.stderr)
        previous_level = LOGGER.level
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        try:
            summary = capture(
                args.channel,
                args.output,
                listen_only=args.listen_only,
                duration=args.duration,
                count=args.count,
                isolated_bench=args.isolated_bench,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"capture failed: {exc}", file=sys.stderr)
            return 2
        finally:
            LOGGER.removeHandler(handler)
            LOGGER.setLevel(previous_level)
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0
    try:
        if args.command == "diff":
            before, after = read_capture(args.baseline), read_capture(args.stimulus)
            document = compare(before, after)
            has_errors = bool(before["errors"] or after["errors"])
        else:
            capture = read_capture(args.log)
            document = (
                inventory(capture)
                if args.command == "inventory"
                else report(capture)
                if args.command == "report"
                else capture
            )
            has_errors = bool(capture["errors"])
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == "report" and args.format == "markdown":
        print(markdown(document), end="")
    else:
        print(json.dumps(document, sort_keys=True, indent=2))
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
