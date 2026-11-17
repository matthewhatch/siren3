"""Bounded ISO discovery with simulator-managed source ownership and raw evidence."""

import hashlib
import json
import logging
import math
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO

from .capture import Frame, decode_identifier
from .live import format_frame, inspect_interface
from .protocol import FAST_PGNS, decode_payload
from .simulator_adapter import AddressManager
from .transport import Reassembler

LOGGER = logging.getLogger(__name__)
QUERY_PGNS = (126996, 126998, 126464)


@dataclass(frozen=True)
class Options:
    channel: str
    output: Path
    target: int
    unique_number: int
    allow_transmit: bool = False
    hardware: bool = False
    isolated_bench: bool = False
    source_address: int = 35
    manufacturer_code: int = 2046
    allow_unassigned_bench_identity: bool = False
    baseline: float = 1.0
    response_window: float = 2.0
    interval: float = 1.0
    recovery: float = 1.0
    duration: float = 30.0

    def validate(self) -> None:
        if not self.allow_transmit:
            raise ValueError("active discovery requires --allow-transmit")
        if not 0 <= self.target <= 251 or not 0 <= self.source_address <= 251:
            raise ValueError("source and target addresses must be 0–251")
        if self.source_address == self.target:
            raise ValueError("preferred source address must differ from target")
        if not 1 <= self.unique_number <= 0x1FFFFF:
            raise ValueError("unique number must be 1–2097151")
        if not (1 <= self.manufacturer_code <= 2044 or self.manufacturer_code == 2046):
            raise ValueError("manufacturer code must be 1–2044, or 2046 for explicit bench testing")
        if self.allow_unassigned_bench_identity and (
            not self.hardware or not self.isolated_bench or self.manufacturer_code != 2046
        ):
            raise ValueError(
                "--allow-unassigned-bench-identity requires --hardware --isolated-bench "
                "and --manufacturer-code 2046"
            )
        for key, low, high in (
            ("baseline", 0.2, 10),
            ("response_window", 0.2, 10),
            ("interval", 1, 10),
            ("recovery", 0.2, 10),
            ("duration", 1, 60),
        ):
            value = getattr(self, key)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{key} must be finite and in [{low}, {high}] seconds")


def preflight(options: Options) -> dict[str, Any]:
    options.validate()
    link = inspect_interface(options.channel, options.isolated_bench, transmit=True)
    if link["linkinfo"]["info_kind"] == "can":
        if not options.hardware:
            raise ValueError("physical transmission additionally requires --hardware")
        if options.manufacturer_code == 2046 and not options.allow_unassigned_bench_identity:
            raise ValueError(
                "unassigned manufacturer 2046 is virtual-only by default; explicitly select "
                "--allow-unassigned-bench-identity with --hardware --isolated-bench "
                "for this isolated bench"
            )
    elif options.hardware:
        raise ValueError("--hardware requires a verified physical CAN link; omit it for vcan")
    return link


def _open_bus(channel: str) -> Any:
    import can

    return can.Bus(
        interface="socketcan",
        channel=channel,
        ignore_config=True,
        receive_own_messages=False,
        local_loopback=True,
        fd=False,
    )


class Session:
    """Clock-driven state machine; only this component can send discovery frames."""

    def __init__(
        self,
        options: Options,
        bus: Any,
        manager: AddressManager,
        stream: BinaryIO,
        started: float,
        stopped: list[str],
        clock: Callable[[], float] | None = None,
        on_frame: Callable[[str, int], None] | None = None,
    ):
        self.options, self.bus, self.manager, self.stream = options, bus, manager, stream
        self.started, self.stopped = started, stopped
        self.clock = clock or time.monotonic
        self.on_frame = on_frame
        self.assembler = Reassembler()
        self.baseline_end = started + options.baseline
        self.last_request = -math.inf
        self.pending: dict[str, Any] | None = None
        self.recovery_end: float | None = None
        self.claim_started = False
        self.claim_sends: list[float] = []
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.lines = 0
        self.tx_count = 0
        self.target_name: int | None = None

    def record(self, message: Any) -> int:
        line = format_frame(message, self.options.channel)
        self.stream.write(line.encode("ascii"))
        self.stream.flush()
        self.lines += 1
        if self.on_frame is not None:
            self.on_frame(line, self.lines)
        return self.lines

    def event(self, kind: str, now: float, **fields: Any) -> None:
        self.events.append({"kind": kind, "elapsed_seconds": now - self.started, **fields})

    def transmit(
        self, pgn: int, data: bytes, source: int, destination: int, now: float, priority: int = 6
    ) -> int:
        import can

        if self.stopped or now - self.started >= self.options.duration:
            raise ValueError("discovery stopped before transmit")
        if self.tx_count >= 24:
            raise ValueError("total transmit budget exhausted")
        if pgn == 60928:
            recent = [t for t in self.claim_sends if now - t < 1]
            if len(recent) >= 4 or len(self.claim_sends) >= 20:
                raise ValueError("claim traffic rate or count limit exceeded; stopping")
            self.claim_sends.append(now)
        elif pgn != 59904 or not self.manager.owns_address or source != self.manager.address:
            raise ValueError("refusing a request from an unclaimed address")
        identifier = self.manager.compose(
            pgn, source=source, destination=destination, priority=priority
        )
        msg = can.Message(
            arbitration_id=identifier,
            is_extended_id=True,
            data=data,
            timestamp=time.time(),
            is_rx=False,
            check=True,
        )
        self.event(
            "send_attempt",
            now,
            pgn=pgn,
            source=source,
            destination=destination,
            payload_hex=data.hex(),
            can_id=f"{identifier:08X}",
        )
        if self.stopped:
            raise ValueError("discovery stopped before transmit")
        self.bus.send(msg, timeout=0.1)
        if pgn == 60928:
            self.manager.claim_sent(self.clock())
        self.tx_count += 1
        line = self.record(msg)
        self.event("send_accepted_by_socket", now, pgn=pgn, line=line)
        return line

    def claims(self, items: list[Any], now: float) -> None:
        for item in items:
            if item.source == self.options.target:
                raise ValueError("source relocation selected target address; stopping before claim")
            self.transmit(item.pgn, item.data, item.source, item.destination, now, item.priority)

    def receive(self, message: Any, now: float) -> None:
        line = self.record(message)
        if (
            not message.is_extended_id
            or message.is_error_frame
            or message.is_remote_frame
            or message.is_fd
        ):
            return
        ident = decode_identifier(message.arbitration_id)
        data = bytes(message.data)
        # All received frames participate in conflict handling, including traffic
        # from other local sockets. Direction is only a candidate origin hint.
        if (
            ident.pgn == 60928
            and ident.source == self.options.target
            and ident.destination == 255
            and len(data) == 8
        ):
            name = int.from_bytes(data, "little")
            if self.target_name is not None and name != self.target_name:
                raise ValueError("target address NAME changed during discovery")
            self.target_name = name
        if self.claim_started:
            previous = self.manager.address
            was_owned = self.manager.owns_address
            self.claims(self.manager.receive(message, now), now)
            if previous != self.manager.address or (was_owned and not self.manager.owns_address):
                self.event(
                    "source_conflict",
                    now,
                    previous_source=previous,
                    new_source=self.manager.address,
                )
                if self.pending is not None:
                    self.pending["status"] = "inconclusive_address_change"
                    self.pending = None
                self.assembler = Reassembler()
        else:
            # Seed occupancy without transmitting during the baseline. Device.receive
            # in NEW state learns observed sources but cannot claim or respond.
            self.manager.receive(message, now)
        request = self.pending
        if request is None or now > request["deadline"]:
            return
        if ident.source != self.options.target or ident.destination not in (
            None,
            255,
            request["source"],
        ):
            return
        if ident.pgn not in (request["pgn"], 59392):
            return
        frames = [
            Frame(
                self.options.channel,
                message.arbitration_id,
                data,
                Decimal(str(message.timestamp)),
                "R" if message.is_rx else "T",
                line_number=line,
            )
        ]
        if ident.pgn in FAST_PGNS:
            try:
                complete = self.assembler.feed(frames[0])
            except ValueError as exc:
                self.event("response_assembly_error", now, line=line, error=str(exc))
                return
            if complete is None:
                return
            data, frames = complete
        try:
            decoded = decode_payload(ident.pgn, data)
        except ValueError as exc:
            self.event("response_decode_error", now, line=line, error=str(exc))
            return
        if ident.pgn == 59392:
            if ident.destination != request["source"] or decoded["requested_pgn"] != request["pgn"]:
                return
            kind = decoded["result"]
        else:
            kind = "response"
        request["observations"].append(
            {
                "kind": kind,
                "pgn": ident.pgn,
                "source": ident.source,
                "destination": ident.destination,
                "decoded": decoded,
                "lines": [frame.line_number for frame in frames],
                "elapsed_seconds": now - self.started,
                "origin_hint": "local_origin"
                if any(f.direction == "T" for f in frames)
                else "bus_receive_unverified",
                "interpretation": "Time-window candidate; sender identity and causality remain unverified.",
            }
        )

    def advance(self, now: float) -> bool:
        if now < self.baseline_end:
            return False
        if not self.claim_started:
            self.event("baseline_complete", now, final_line=self.lines)
            self.claim_started = True
            self.manager.reserve(self.options.target)
            self.claims(self.manager.start(now), now)
            self.event("claim_started", now, source=self.manager.address)
            return False
        self.claims(self.manager.tick(now), now)
        if self.pending is not None and now >= self.pending["deadline"]:
            request = self.pending
            kinds = {o["kind"] for o in request["observations"]}
            negative_ack = kinds & {"nack", "access_denied", "busy"}
            if ("response" in kinds and negative_ack) or len(kinds - {"response"}) > 1:
                request["status"] = "inconclusive_conflicting_observations"
            elif "response" in kinds:
                functions = {
                    o["decoded"].get("function")
                    for o in request["observations"]
                    if o["kind"] == "response"
                }
                request["status"] = (
                    "response_observed"
                    if request["pgn"] != 126464 or {"transmit", "receive"} <= functions
                    else "partial_pgn_lists"
                )
            elif kinds:
                request["status"] = "acknowledgement_observed"
            else:
                request["status"] = "timeout"
            self.event("request_window_complete", now, pgn=request["pgn"], status=request["status"])
            self.pending = None
            self.assembler = Reassembler()
        if (
            self.pending is None
            and len(self.requests) < len(QUERY_PGNS)
            and self.manager.owns_address
            and now - self.last_request >= self.options.interval
        ):
            pgn = QUERY_PGNS[len(self.requests)]
            source = self.manager.address
            line = self.transmit(59904, pgn.to_bytes(3, "little"), source, self.options.target, now)
            request = {
                "pgn": pgn,
                "source": source,
                "target": self.options.target,
                "sent_elapsed_seconds": now - self.started,
                "deadline": now + self.options.response_window,
                "request_line": line,
                "status": "pending",
                "observations": [],
            }
            self.requests.append(request)
            self.pending, self.last_request = request, now
        if len(self.requests) == len(QUERY_PGNS) and self.pending is None:
            if self.recovery_end is None:
                self.recovery_end = now + self.options.recovery
                self.event("recovery_started", now)
            return now >= self.recovery_end
        return False


def discover(
    options: Options, *, on_frame: Callable[[str, int], None] | None = None
) -> dict[str, Any]:
    link = preflight(options)
    manager = AddressManager(
        options.manufacturer_code, options.unique_number, options.source_address
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "mode": "active_discovery",
        "channel": options.channel,
        "target": options.target,
        "interface_before": link,
        "output": str(options.output),
        "iso_name": f"{manager.name:016x}",
        "simulator": manager.provenance,
        "tx_evidence": "T lines record successful socket submissions, not confirmed wire delivery.",
        "limits": {
            "duration": options.duration,
            "baseline": options.baseline,
            "response_window": options.response_window,
            "interval": options.interval,
            "recovery": options.recovery,
            "max_requests": 3,
            "max_claims": 20,
            "max_claims_per_second": 4,
        },
        "hardware_selected": options.hardware,
        "isolated_bench_attested": options.isolated_bench,
        "unique_number": options.unique_number,
        "manufacturer_code": options.manufacturer_code,
        "allow_unassigned_bench_identity": options.allow_unassigned_bench_identity,
        "identity_status": (
            "unassigned_isolated_bench_exception"
            if options.allow_unassigned_bench_identity
            else "unassigned_virtual"
            if options.manufacturer_code == 2046
            else "operator_supplied_manufacturer_code"
        ),
        "started_utc": datetime.now(UTC).isoformat(),
        "stop_reason": "startup_error",
        "requests": [],
        "events": [],
        "capture_created": False,
        "unresolved_questions": [
            "Candidate responses are correlated by source/PGN/destination/window, not proven causal.",
            "T/R markings, socket send success and bus ACKs do not prove remote processing.",
            "No response before timeout does not imply unsupported capability.",
            "Sender identity, capture completeness and continued interface settings remain unverified.",
        ],
    }
    stopped: list[str] = []
    handlers: dict[Any, Any] = {}
    bus = None
    session: Session | None = None
    started = time.monotonic()
    sidecar = Path(str(options.output) + ".json")
    with sidecar.open("x", encoding="utf-8") as metadata:

        def stop(signum: int, _frame: Any) -> None:
            stopped.append(signal.Signals(signum).name)

        try:
            with options.output.open("xb") as stream:
                summary["capture_created"] = True
                for signum in (signal.SIGINT, signal.SIGTERM):
                    handlers[signum] = signal.signal(signum, stop)
                bus = _open_bus(options.channel)
                started = time.monotonic()
                session = Session(
                    options, bus, manager, stream, started, stopped, on_frame=on_frame
                )
                LOGGER.info("Discovery baseline on %s; stop with Ctrl-C", options.channel)
                while not stopped:
                    now = time.monotonic()
                    if now - started >= options.duration:
                        summary["stop_reason"] = "duration"
                        break
                    # Drain a bounded batch before ownership-dependent transmissions.
                    # If the queue stays busy, continue receiving rather than send while
                    # an unprocessed address-conflict frame may still be queued.
                    message = bus.recv(timeout=0.02)
                    drained = False
                    for index in range(64):
                        now = time.monotonic()
                        if stopped or now - started >= options.duration:
                            break
                        if message is None:
                            drained = True
                            break
                        session.receive(message, now)
                        if index < 63:
                            message = bus.recv(timeout=0)
                    if stopped:
                        break
                    if not drained:
                        continue
                    if session.advance(now):
                        summary["stop_reason"] = "complete"
                        break
                if stopped:
                    summary["stop_reason"] = stopped[0]
        except KeyboardInterrupt:
            summary["stop_reason"] = "SIGINT"
        except Exception as exc:
            summary["stop_reason"], summary["error"] = "error", str(exc)
            raise RuntimeError(f"discovery stopped: {exc}") from exc
        finally:
            try:
                if bus is not None:
                    bus.shutdown()
            except Exception as exc:
                summary["stop_reason"], summary["error"] = "error", str(exc)
                raise RuntimeError(f"discovery shutdown failed: {exc}") from exc
            finally:
                if session is not None:
                    for request in session.requests:
                        if request["status"] == "pending":
                            request["status"] = "inconclusive_stopped"
                        request.pop("deadline", None)
                    summary.update(
                        requests=session.requests,
                        events=session.events,
                        frames=session.lines,
                        tx_frames=session.tx_count,
                        final_source=manager.address,
                    )
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
                summary["ended_utc"] = datetime.now(UTC).isoformat()
                summary["elapsed_seconds"] = time.monotonic() - started
                summary["sha256"] = None
                if summary["capture_created"]:
                    try:
                        with options.output.open("rb") as raw:
                            summary["sha256"] = hashlib.file_digest(raw, "sha256").hexdigest()
                    except OSError as exc:
                        summary["hash_error"] = str(exc)
                metadata.write(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    return summary
