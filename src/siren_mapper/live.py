"""Explicit, receive-only SocketCAN capture. Never configures a link or sends."""

import hashlib
import json
import logging
import math
import re
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def inspect_interface(channel: str, isolated_bench: bool = False) -> dict[str, Any]:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", channel) or channel.startswith("-"):
        raise ValueError("select one explicit Linux interface name")
    try:
        result = subprocess.run(
            ["ip", "-json", "-details", "link", "show", "dev", channel],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        links = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError(f"cannot verify interface {channel}: {exc}") from exc
    if not isinstance(links, list) or len(links) != 1 or not isinstance(links[0], dict):
        raise ValueError("unexpected interface metadata")
    link = links[0]
    flags = link.get("flags", [])
    if link.get("ifname") != channel or not isinstance(flags, list) or "UP" not in flags:
        raise ValueError("selected interface must exist and be UP")
    info = link.get("linkinfo", {})
    kind = info.get("info_kind") if isinstance(info, dict) else None
    if kind == "vcan":
        return link
    if kind != "can":
        raise ValueError("only verified can or vcan interfaces are supported")
    data = info.get("info_data", {})
    timing = data.get("bittiming", {}) if isinstance(data, dict) else {}
    if not isinstance(timing, dict) or timing.get("bitrate") != 250000:
        raise ValueError("physical CAN must already be configured at 250000 bit/s")
    modes = data.get("ctrlmode", [])
    if not isinstance(modes, list) or "LISTEN-ONLY" not in modes:
        raise ValueError("physical CAN must already have kernel LISTEN-ONLY enabled")
    if any(mode in modes for mode in ("LOOPBACK", "FD", "FD-NON-ISO", "PRESUME-ACK")):
        raise ValueError("physical capture requires classical CAN without loopback or presume-ack")
    if data.get("state") not in ("ERROR-ACTIVE", "ERROR-WARNING", "ERROR-PASSIVE"):
        raise ValueError("physical CAN controller is not ready for capture")
    if not isolated_bench:
        raise ValueError("--isolated-bench must confirm isolation and verified termination")
    return link


def _open_receiver(channel: str) -> Any:
    # Lazy import keeps offline workflows independent of python-can installation/config.
    try:
        import can
    except ImportError as exc:
        raise ValueError("install the hardware extra: pip install -e '.[hardware]'") from exc
    return can.Bus(
        interface="socketcan",
        channel=channel,
        ignore_config=True,
        receive_own_messages=False,
        local_loopback=False,
        fd=False,
    )


def format_frame(message: Any, channel: str) -> str:
    """candump -L style; retain unsupported frame kinds for later diagnostic replay."""
    identifier = message.arbitration_id | (0x20000000 if message.is_error_frame else 0)
    width = 8 if message.is_extended_id or message.is_error_frame else 3
    if message.is_remote_frame:
        payload = f"R{message.dlc}"
    elif message.is_fd:
        flags = int(message.bitrate_switch) | (int(message.error_state_indicator) << 1)
        payload = f"#{flags:X}{bytes(message.data).hex().upper()}"
    else:
        payload = bytes(message.data).hex().upper()
    direction = "R" if message.is_rx else "T"
    return f"({message.timestamp:.6f}) {channel} {identifier:0{width}X}#{payload} {direction}\n"


def capture(
    channel: str,
    output: Path,
    *,
    listen_only: bool,
    duration: float = 60,
    count: int | None = None,
    isolated_bench: bool = False,
) -> dict[str, Any]:
    if not listen_only:
        raise ValueError("capture requires explicit --listen-only")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    if count is not None and count < 1:
        raise ValueError("count must be positive")
    link = inspect_interface(channel, isolated_bench)
    sidecar = Path(str(output) + ".json")
    # Both files are exclusive-create. A collision never truncates evidence.
    # Reserve metadata first, then data; keep an honest diagnostic on partial startup.
    with sidecar.open("x", encoding="utf-8") as metadata:
        summary: dict[str, Any] = {
            "schema_version": 1,
            "mode": "passive",
            "channel": channel,
            "output": str(output),
            "interface_before": link,
            "isolated_bench_attested": isolated_bench,
            "started_utc": datetime.now(UTC).isoformat(),
            "duration_limit_seconds": duration,
            "count_limit": count,
            "frames": 0,
            "unsupported_frames": 0,
            "stop_reason": "startup_error",
            "timestamp_precision": "microseconds from python-can receive timestamp",
            "unresolved_questions": [
                "T/R flags do not prove remote origin or device application processing.",
                "Capture completeness and kernel receive drops are not measured.",
                "Interface settings are checked before opening; external reconfiguration is unmonitored.",
            ],
        }
        data_created = False
        bus = None
        old_handlers: dict[Any, Any] = {}
        stopped: list[str] = []
        started = time.monotonic()

        def stop(signum: int, _frame: Any) -> None:
            stopped.append(signal.Signals(signum).name)

        try:
            with output.open("xb") as stream:
                data_created = True
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.signal(signum, stop)
                bus = _open_receiver(channel)
                LOGGER.info("Listening on %s; writing %s; stop with Ctrl-C", channel, output)
                started = time.monotonic()
                summary["stop_reason"] = "duration"
                while not stopped:
                    remaining = duration - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    message = bus.recv(timeout=min(0.1, remaining))
                    if message is None:
                        continue
                    line = format_frame(message, channel).encode("ascii")
                    stream.write(line)
                    stream.flush()
                    summary["frames"] += 1
                    if (
                        message.is_fd
                        or message.is_error_frame
                        or message.is_remote_frame
                        or not message.is_extended_id
                    ):
                        summary["unsupported_frames"] += 1
                    if count is not None and summary["frames"] >= count:
                        summary["stop_reason"] = "count"
                        break
                if stopped:
                    summary["stop_reason"] = stopped[0]
        except KeyboardInterrupt:
            summary["stop_reason"] = "SIGINT"
        except Exception as exc:
            summary["stop_reason"] = "error"
            summary["error"] = str(exc)
            raise RuntimeError(f"capture stopped: {exc}") from exc
        finally:
            try:
                if bus is not None:
                    bus.shutdown()
            except Exception as exc:
                summary["stop_reason"] = "error"
                summary["error"] = f"receiver shutdown failed: {exc}"
                raise RuntimeError(summary["error"]) from exc
            finally:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)
                summary["ended_utc"] = datetime.now(UTC).isoformat()
                summary["elapsed_seconds"] = time.monotonic() - started
                summary["capture_created"] = data_created
                summary["sha256"] = None
                if data_created:
                    try:
                        with output.open("rb") as captured:
                            summary["sha256"] = hashlib.file_digest(captured, "sha256").hexdigest()
                    except OSError as exc:
                        summary["hash_error"] = str(exc)
                metadata.write(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    LOGGER.info("Capture stopped: %s; %s frame(s)", summary["stop_reason"], summary["frames"])
    return summary
