"""Human presentation of the shared packet decoder, with no transport or TX API."""

import json
import math
import time
from pathlib import Path
from typing import Any, TextIO

from .capture import parse_line
from .decoder import PacketDecoder, PacketEvent
from .protocol import FAST_PGNS, is_proprietary, pgn_name

LABELS = {
    "engine_instance": ("Engine instance", ""),
    "rpm": ("Engine speed", "RPM"),
    "boost_pressure_pa": ("Boost pressure", "Pa"),
    "trim_pct": ("Tilt/trim", "%"),
    "oil_pressure_pa": ("Oil pressure", "Pa"),
    "oil_temperature_k": ("Oil temperature", "K"),
    "coolant_temperature_k": ("Coolant temperature", "K"),
    "alternator_voltage_v": ("Alternator voltage", "V"),
    "fuel_rate_lph": ("Fuel rate", "L/h"),
    "engine_time_s": ("Engine running time", "s"),
    "coolant_pressure_pa": ("Coolant pressure", "Pa"),
    "fuel_pressure_pa": ("Fuel pressure", "Pa"),
    "engine_load_pct": ("Engine load", "%"),
    "engine_torque_pct": ("Engine torque", "%"),
    "level_pct": ("Fluid level", "%"),
    "capacity_l": ("Tank capacity", "L"),
    "interval_ms": ("Interval", "ms"),
    "sequence": ("Sequence", ""),
    "model": ("Model", ""),
    "software": ("Software", ""),
    "serial": ("Serial", ""),
    "nmea_version": ("NMEA database version (raw)", ""),
    "load_equivalency": ("Load equivalency", "LEN"),
    "controller1": ("Controller 1", ""),
    "controller2": ("Controller 2", ""),
    "equipment_status": ("Equipment status", ""),
    "name_hex": ("ISO NAME", ""),
    "requested_pgn": ("Requested PGN", ""),
    "reported_status_flags": ("Reported status flags", ""),
}
CONTROLLER = {0: "error active", 1: "error passive", 2: "bus off", 3: "unavailable"}
EQUIPMENT = {0: "operational", 1: "fault", 2: "reserved/unknown enum", 3: "unavailable"}


def value_text(key: str, value: Any) -> str:
    if value is None:
        return "unavailable/invalid (wire sentinel or absent value)"
    if key in ("controller1", "controller2"):
        return f"{CONTROLLER.get(value, 'unknown enum')} ({value})"
    if key == "equipment_status":
        return f"{EQUIPMENT.get(value, 'unknown enum')} ({value})"
    if key == "requested_pgn":
        return f"{value} ({pgn_name(value)})"
    if key in ("status1", "status2", "status2_reserved_bits"):
        return f"0x{value:04X}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none reported"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)


def fields_text(fields: dict[str, Any], *, verbose: bool) -> list[str]:
    result = []
    for key, value in fields.items():
        if not verbose and ("reserved" in key or key in ("status1", "status2")):
            continue
        label, unit = LABELS.get(key, (key.replace("_", " ").capitalize(), ""))
        text = value_text(key, value)
        if value is not None and unit:
            text += " " + unit
        if key == "interval_ms" and value is not None:
            text += f" ({value / 1000:g} s)"
        result.append(f"{label}={text}")
    return result


def interpretation(event: PacketEvent) -> str:
    pgn = event.frame.identifier.pgn
    if event.status == "pending":
        data = event.frame.data
        return (
            f"Fast packet seq={data[0] >> 5} frame={data[0] & 31} pending; no decoded fields yet."
        )
    if event.status == "malformed":
        return "Malformed packet; " + "; ".join(event.errors)
    if event.status == "unknown":
        if is_proprietary(pgn):
            return "Proprietary payload; vendor meaning unknown, bytes not interpreted."
        return "Payload layout unsupported/unknown; bytes not interpreted."
    decoded = event.decoded or {}
    if pgn == 59904:
        target = decoded["requested_pgn"]
        return f"Request {pgn_name(target)} (PGN {target}); processing unverified."
    if pgn == 59392:
        label = {
            "ack": "Positive ACK",
            "nack": "Negative ACK",
            "busy": "Busy acknowledgement",
            "access_denied": "Access denied acknowledgement",
        }[decoded["result"]]
        target = decoded["requested_pgn"]
        return f"{label} for {pgn_name(target)} (PGN {target})."
    if pgn == 60928:
        return "Address claim observed; address ownership is unverified."
    if pgn == 126464:
        return f"Advertised {decoded['function']} PGN list; active processing is unknown."
    if pgn == 126996:
        return "Product information message observed."
    if pgn == 126993:
        return "Heartbeat message observed; no application-processing claim."
    if pgn == 126998:
        return "Configuration information message observed; no configuration changed."
    if pgn in (127488, 127489):
        return "Engine parameters reported; device processing is unverified."
    if pgn == 127505:
        return "Tank fluid measurement reported."
    return "Message observed."


def format_event(event: PacketEvent, *, verbose: bool = False, raw: bool = False) -> str:
    frame = event.frame
    ident = frame.identifier
    stamp = str(frame.timestamp) if frame.timestamp is not None else "timestamp=?"
    direction = {"T": "TX/local (origin unverified)", "R": "RX (origin unverified)"}.get(
        frame.direction, "direction=?"
    )
    destination = "ALL" if ident.destination in (None, 255) else str(ident.destination)
    channel = json.dumps(frame.channel, ensure_ascii=True)[1:-1]
    header = (
        f"{stamp} {channel} {direction} priority={ident.priority} "
        f"src={ident.source} dst={destination} PGN={ident.pgn} {pgn_name(ident.pgn)} "
        f"len={len(frame.data)}"
    )
    if ident.pgn in FAST_PGNS and event.payload is not None:
        header += f" [fast packet complete: {len(event.frames)} frames, {len(event.payload)} bytes]"
    fields = event.decoded if event.status == "decoded" else event.partial
    detail = fields_text(fields or {}, verbose=verbose)
    text = interpretation(event).rstrip(".")
    if event.partial:
        text += "; known prefix only (remaining fields not decoded)"
    if raw:
        text += f"; CAN ID={frame.can_id:08X} data={frame.data.hex().upper()}"
        if event.payload is not None and ident.pgn in FAST_PGNS:
            text += f"; assembled={event.payload.hex().upper()}"
    if verbose:
        lines = [header, "  " + text, *("  " + item for item in detail)]
        if event.frames:
            lines.append("  Capture lines=" + ", ".join(str(f.line_number) for f in event.frames))
        return "\n".join(lines)
    return header + " | " + text + (" | " + "; ".join(detail) if detail else "")


class HumanInterpreter:
    def __init__(self, output: TextIO, *, verbose: bool = False, raw: bool = False):
        self.output, self.verbose, self.raw = output, verbose, raw
        self.decoder = PacketDecoder()
        self.parse_errors = 0

    def feed_line(self, line: str, number: int) -> None:
        try:
            frame = parse_line(line, number)
        except ValueError as exc:
            self.parse_errors += 1
            self.output.write(
                f"Line {number}: malformed CAN frame: {exc}"
                + (f"; raw={json.dumps(line.rstrip())}" if self.raw else "")
                + "\n"
            )
            self.output.flush()
            return
        if frame is None:
            return
        event = self.decoder.feed(frame)
        self.output.write(format_event(event, verbose=self.verbose, raw=self.raw) + "\n")
        # Expired streams may be diagnosed while a different valid packet is fed.
        if event.status != "malformed":
            for error in event.errors:
                self.output.write(f"Line {number}: incomplete/timeout: {error}\n")
        self.output.flush()

    def finish(self) -> int:
        for error in self.decoder.finish():
            self.output.write(f"Incomplete session: {error}\n")
        self.output.flush()
        return 1 if self.parse_errors or self.decoder.error_count else 0


def replay(
    path: Path, output: TextIO, *, verbose: bool = False, raw: bool = False, speed: float = 0
) -> int:
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("speed must be finite and nonnegative (0 = no delays)")
    interpreter = HumanInterpreter(output, verbose=verbose, raw=raw)
    previous = None
    try:
        with path.open(encoding="ascii") as stream:
            for number, line in enumerate(stream, 1):
                if speed:
                    try:
                        frame = parse_line(line, number)
                    except ValueError:
                        frame = None
                    if frame is not None and frame.timestamp is not None:
                        if previous is not None:
                            delay = max(0, float(frame.timestamp - previous) / speed)
                            if not math.isfinite(delay):
                                raise ValueError("timestamp gap is too large for paced replay")
                            while delay > 0:
                                wait = min(delay, 0.1)
                                time.sleep(wait)
                                delay -= wait
                        previous = frame.timestamp
                interpreter.feed_line(line, number)
    except KeyboardInterrupt:
        output.write("Replay interrupted.\n")
    return interpreter.finish()
