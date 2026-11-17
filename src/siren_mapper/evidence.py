"""Read-only capture evidence with stable identifiers and explicit uncertainty."""

import hashlib
from pathlib import Path
from typing import Any

from .capture import parse_line
from .decoder import PacketDecoder


def read_capture(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_frames: list[dict[str, Any]] = []
    decoder = PacketDecoder()
    for number, line in enumerate(raw.decode("ascii").splitlines(), 1):
        try:
            frame = parse_line(line, number)
            if frame is None:
                continue
            ident = frame.identifier
            raw_frames.append(
                {
                    "line": number,
                    "raw": frame.raw,
                    "channel": frame.channel,
                    "timestamp": str(frame.timestamp) if frame.timestamp is not None else None,
                    "direction": frame.direction,
                    "can_id": f"{frame.can_id:08X}",
                    "pgn": ident.pgn,
                    "source": ident.source,
                    "destination": ident.destination,
                    "payload_hex": frame.data.hex(),
                }
            )
            event = decoder.feed(frame)
            errors.extend({"line": number, "error": issue} for issue in event.errors)
            if event.payload is None:
                continue
            data, frames = event.payload, event.frames
            record = {
                "pgn": ident.pgn,
                "priority": ident.priority,
                "source": ident.source,
                "destination": ident.destination,
                "channel": frame.channel,
                "payload_hex": data.hex(),
                "file": str(path),
                "sha256": digest,
                "frames": [
                    {
                        "line": f.line_number,
                        "raw": f.raw,
                        "timestamp": str(f.timestamp) if f.timestamp is not None else None,
                        "direction": f.direction,
                        "can_id": f"{f.can_id:08X}",
                    }
                    for f in frames
                ],
            }
            record["status"] = event.status
            if event.decoded is not None:
                record["decoded"] = event.decoded
            if event.partial:
                record["partial_decoded"] = event.partial
            if event.status == "malformed":
                record["error"] = event.errors[0]
            records.append(record)
        except ValueError as exc:
            errors.append({"line": number, "error": str(exc)})
    errors.extend({"line": None, "error": issue} for issue in decoder.finish())
    for index, record in enumerate(records, 1):
        record["evidence_id"] = f"message-{index}"
        record["timestamp"] = record["frames"][-1]["timestamp"]
        record["can_id"] = record["frames"][-1]["can_id"]
        record["provenance"] = {
            "file": str(path),
            "sha256": digest,
            "lines": [frame["line"] for frame in record["frames"]],
        }
        record["classification"] = "decoded_fact" if record["status"] == "decoded" else "unknown"
        record["confidence"] = {
            "level": "supported" if record["status"] == "decoded" else "unresolved",
            "basis": (
                "Offline wire-layout validation; device behavior and identity ownership unverified."
                if record["status"] == "decoded"
                else "Raw evidence retained; payload layout is unknown or failed validation."
            ),
        }
        questions = [
            "Does the source address identify the same device throughout this capture?",
            "Do these frames include local echo? Direction flags alone cannot establish origin.",
        ]
        if any(frame["timestamp"] is None for frame in record["frames"]):
            questions.append("Capture timing is unavailable for one or more frames.")
        if record["status"] == "unknown":
            questions.append("What is the layout and meaning of the undecoded payload?")
        if record["status"] == "malformed":
            questions.append("What explains the payload anomaly: " + record["error"] + "?")
        if record["pgn"] == 126464:
            questions.append("Which advertised PGNs are actively configured or processed?")
        record["unresolved_questions"] = questions
    lines = raw.decode("ascii").splitlines()
    for error in errors:
        number = error["line"]
        error["raw"] = lines[number - 1] if number is not None else None
        error["provenance"] = {"file": str(path), "sha256": digest, "line": number}
    return {
        "schema_version": 1,
        "capture": {"file": str(path), "sha256": digest, "bytes": len(raw)},
        "records": records,
        "errors": errors,
        "raw_frames": raw_frames,
        "unresolved_questions": [
            "Collection setup, device ownership of source addresses, and capture completeness are unverified.",
            "Observed traffic does not establish internal application processing or stimulus causality.",
        ],
    }
