"""Read-only discovery entry points and versioned profile presentation."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .profiles import (
    diff_profiles,
    load_profile,
    profile_from_capture,
    profile_markdown,
    save_profile,
)


def summary(profile: dict[str, Any]) -> str:
    lines = []
    for device in profile["devices"]:
        identity = device["identity"]
        where = ", ".join(
            f"{json.dumps(p['channel'])[1:-1]} source {p['source']}" for p in device["locations"]
        )
        lines.append(
            f"Device {identity['name_hex'] or 'unknown NAME'} | {where} | {identity['confidence']}"
        )
        lines.append("  Observed PGNs: " + ", ".join(map(str, device["observed_pgns"])))
        lines.append("  Last capture timestamp: " + (device.get("last_timestamp") or "unknown"))
        product = device["product"]["fields"]
        if product:
            lines.append(
                f"  Model={json.dumps(product.get('model'))} software={json.dumps(product.get('software'))} serial={json.dumps(product.get('serial'))}"
            )
        lines.append("  Configuration: " + device["configuration"]["status"].replace("_", " "))
        for role in ("transmit", "receive"):
            section = device["advertised_pgns"][role]
            pg = (
                ", ".join(map(str, section["pgns"]))
                if section["pgns"] is not None
                else section["status"].replace("_", " ")
            )
            lines.append(f"  Advertised {role} PGNs: {pg}")
    if not lines:
        lines.append("No device source observed; request destinations alone do not prove presence.")
    lines.append(
        "Observed identity claims/candidate replies only; internal processing remains unverified."
    )
    return "\n".join(lines) + "\n"


def print_profile(profile: dict[str, Any], format_name: str, *, output: Any = None) -> None:
    stream = output or sys.stdout
    if format_name == "markdown":
        print(profile_markdown(profile), end="", file=stream)
    elif format_name == "human":
        print(summary(profile), end="", file=stream)
    else:
        print(json.dumps(profile, sort_keys=True, indent=2), file=stream)


def choose_profile_path(capture: Path) -> Path:
    base = Path(str(capture) + ".profile.json")
    index = 1
    while base.exists() or Path(str(base) + ".md").exists():
        index += 1
        base = Path(str(capture) + f".profile-{index:03d}.json")
    return base


def add_commands(commands: Any) -> None:
    devices = commands.add_parser("devices", help="passive observed-node inventory (no CAN TX)")
    mode = devices.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", type=Path)
    mode.add_argument("--channel", "--interface", dest="channel")
    devices.add_argument("--output", type=Path)
    devices.add_argument("--profile-output", type=Path)
    devices.add_argument("--isolated-bench", action="store_true")
    devices.add_argument("--duration", type=float, default=60)
    devices.add_argument("--count", type=int)
    devices.add_argument("--format", choices=("human", "json", "markdown"), default="human")
    devices.add_argument("--human", action="store_true")
    devices.add_argument("--verbose", action="store_true")
    devices.add_argument("--raw", action="store_true")
    profile = commands.add_parser("profile", help="show or compare versioned device snapshots")
    operations = profile.add_subparsers(dest="operation", required=True)
    show = operations.add_parser("show")
    show.add_argument("path", type=Path)
    show.add_argument("--format", choices=("human", "json", "markdown"), default="human")
    diff = operations.add_parser("diff")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("current", type=Path)


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "profile":
        try:
            if args.operation == "show":
                print_profile(load_profile(args.path), args.format)
            else:
                print(
                    json.dumps(
                        diff_profiles(load_profile(args.baseline), load_profile(args.current)),
                        sort_keys=True,
                        indent=2,
                    )
                )
            return 0
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command not in ("discover", "devices"):
        return None
    capture = args.capture
    channel = args.channel
    if capture is not None and channel is not None:
        parser.error("choose --capture or --interface/--channel, not both")
    if capture is None and channel is None:
        parser.error("choose offline --capture or an explicit live --interface/--channel")
    transmit = args.command == "discover" and args.allow_transmit
    if capture is not None and (
        transmit
        or getattr(args, "hardware", False)
        or getattr(args, "allow_unassigned_bench_identity", False)
    ):
        parser.error("offline --capture cannot be combined with hardware/transmit flags")
    if transmit:
        if args.count is not None:
            parser.error("--count is for passive mode; active discovery has bounded query counts")
        if args.target is None or args.unique_number is None or args.output is None:
            parser.error("active discovery requires --target --unique-number --output")
        return None  # existing address-managed transmitter handles this mode
    if args.command == "discover" and (
        args.unique_number is not None or args.hardware or args.allow_unassigned_bench_identity
    ):
        parser.error(
            "identity/hardware transmit options require explicit --allow-transmit/--allow-tx"
        )
    if capture is not None and args.output is not None:
        parser.error("offline profile export uses --profile-output, not --output")
    if capture is None and args.output is None:
        parser.error("live passive discovery requires --output to retain raw capture evidence")
    try:
        if capture is None:
            from .live import capture as passive_capture

            view = None
            if args.human or args.verbose or args.raw:
                from .interpreter import HumanInterpreter

                view = HumanInterpreter(sys.stdout, verbose=args.verbose, raw=args.raw)
            try:
                passive_capture(
                    channel,
                    args.output,
                    listen_only=True,
                    duration=args.duration,
                    count=getattr(args, "count", None),
                    isolated_bench=args.isolated_bench,
                    on_frame=view.feed_line if view else None,
                )
            finally:
                if view is not None:
                    view.finish()
            capture = args.output
        elif args.human or args.verbose or args.raw:
            from .interpreter import replay

            replay(capture, sys.stdout, verbose=args.verbose, raw=args.raw)
        profile = profile_from_capture(
            capture,
            target=getattr(args, "target", None),
            response_window=getattr(args, "response_window", 2),
        )
        destination = args.profile_output or choose_profile_path(capture)
        save_profile(profile, destination)
        print(f"Profile saved: {destination}; Markdown: {destination}.md", file=sys.stderr)
        human = args.human or args.verbose or args.raw
        print_profile(
            profile, "human" if human else args.format, output=sys.stderr if human else sys.stdout
        )
        return 1 if profile["errors"] else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"discovery failed: {exc}", file=sys.stderr)
        return 2
