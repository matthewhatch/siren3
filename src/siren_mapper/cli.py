"""Offline evidence commands and explicitly selected live modes."""

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
    for command in ("decode", "inventory", "report", "replay"):
        subparser = commands.add_parser(command, help=f"{command} a local candump file")
        subparser.add_argument("log", type=Path)
        if command in ("decode", "replay"):
            subparser.add_argument("--human", action="store_true")
            subparser.add_argument("--verbose", action="store_true")
            subparser.add_argument("--raw", action="store_true")
        if command == "replay":
            subparser.add_argument(
                "--speed",
                type=float,
                default=0,
                help="timestamp playback rate; 0 = no delays (default)",
            )
        if command == "report":
            subparser.add_argument("--format", choices=("markdown", "json"), default="json")
    diff = commands.add_parser(
        "diff", help="compare offline observations without inferring causality"
    )
    diff.add_argument("baseline", type=Path)
    diff.add_argument("stimulus", type=Path)
    live = commands.add_parser("capture", help="explicit passive SocketCAN capture")
    live.add_argument("--channel", "--interface", dest="channel", required=True)
    live.add_argument("--listen-only", action="store_true", required=True)
    live.add_argument(
        "--isolated-bench",
        action="store_true",
        help="confirm an isolated bench with verified termination (physical CAN)",
    )
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--duration", type=float, default=60)
    live.add_argument("--count", type=int)
    active = commands.add_parser("discover", help="bounded, explicitly authorized ISO discovery")
    active.add_argument("--channel", "--interface", dest="channel")
    active.add_argument("--capture", type=Path)
    active.add_argument("--profile-output", type=Path)
    active.add_argument("--format", choices=("json", "markdown", "human"), default="json")
    active.add_argument("--target", type=int)
    active.add_argument("--unique-number", type=int)
    active.add_argument(
        "--allow-transmit", "--allow-tx", dest="allow_transmit", action="store_true"
    )
    active.add_argument("--hardware", action="store_true")
    active.add_argument(
        "--allow-unassigned-bench-identity",
        action="store_true",
        help="permit manufacturer 2046 only with --hardware --isolated-bench",
    )
    active.add_argument("--isolated-bench", action="store_true")
    active.add_argument("--output", type=Path)
    active.add_argument("--source-address", type=int, default=35)
    active.add_argument("--manufacturer-code", type=int, default=2046)
    for name, default in (
        ("device-instance", 0),
        ("device-function", 140),
        ("device-class", 50),
        ("system-instance", 0),
        ("industry-group", 4),
    ):
        active.add_argument("--" + name, type=int, default=default)
    active.add_argument("--baseline", type=float, default=1.0)
    active.add_argument("--response-window", type=float, default=2.0)
    active.add_argument("--interval", type=float, default=1.0)
    active.add_argument("--recovery", type=float, default=1.0)
    active.add_argument("--duration", type=float, default=30.0)
    active.add_argument("--count", type=int, help="frame limit for passive mode only")
    for live_parser in (live, active):
        live_parser.add_argument("--human", action="store_true")
        live_parser.add_argument("--verbose", action="store_true")
        live_parser.add_argument("--raw", action="store_true")
    from .device_commands import add_commands, execute

    add_commands(commands)
    args = parser.parse_args(argv)
    result = execute(args, parser)
    if result is not None:
        return result
    human = any(getattr(args, key, False) for key in ("human", "verbose", "raw"))
    if args.command == "replay" or (args.command == "decode" and human):
        from .interpreter import replay

        try:
            return replay(
                args.log,
                sys.stdout,
                verbose=args.verbose,
                raw=args.raw,
                speed=getattr(args, "speed", 0),
            )
        except (OSError, ValueError, UnicodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command in ("capture", "discover"):
        if args.command == "capture":
            from .live import LOGGER, capture
        else:
            from .discovery import LOGGER, Options, discover

        view = None
        if human:
            from .interpreter import HumanInterpreter

            view = HumanInterpreter(sys.stdout, verbose=args.verbose, raw=args.raw)
        human_status = 0
        handler = logging.StreamHandler(sys.stderr)
        previous_level = LOGGER.level
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        try:
            if args.command == "capture":
                summary = capture(
                    args.channel,
                    args.output,
                    listen_only=args.listen_only,
                    duration=args.duration,
                    count=args.count,
                    isolated_bench=args.isolated_bench,
                    on_frame=view.feed_line if view is not None else None,
                )
            else:
                options = Options(
                    **{
                        key: value
                        for key, value in vars(args).items()
                        if key
                        not in (
                            "command",
                            "human",
                            "verbose",
                            "raw",
                            "capture",
                            "format",
                            "count",
                        )
                    }
                )
                summary = (
                    discover(options, on_frame=view.feed_line)
                    if view is not None
                    else discover(options)
                )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"{args.command} failed: {exc}", file=sys.stderr)
            return 2
        finally:
            LOGGER.removeHandler(handler)
            LOGGER.setLevel(previous_level)
            if view is not None:
                human_status = view.finish()
        if args.command == "discover" and "profile" in summary:
            from .device_commands import print_profile
            from .profiles import load_profile

            if human:
                print_profile(load_profile(Path(summary["profile"])), "human", output=sys.stderr)
            elif args.format != "json":
                print_profile(load_profile(Path(summary["profile"])), args.format)
                return human_status
        print(
            json.dumps(summary, sort_keys=True, indent=2), file=sys.stderr if human else sys.stdout
        )
        return human_status
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
