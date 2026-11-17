"""Shared incremental decode pipeline for evidence, human replay and live display."""

from dataclasses import dataclass, field
from typing import Any

from .capture import Frame
from .protocol import FAST_PGNS, PGNS, decode_payload, partial_payload
from .transport import Reassembler


@dataclass
class PacketEvent:
    frame: Frame
    status: str
    frames: list[Frame] = field(default_factory=list)
    payload: bytes | None = None
    decoded: dict[str, Any] | None = None
    partial: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class PacketDecoder:
    def __init__(self) -> None:
        self.assembler = Reassembler()
        self.error_count = 0

    def feed(self, frame: Frame) -> PacketEvent:
        pgn = frame.identifier.pgn
        event = PacketEvent(frame, "pending", [frame])
        try:
            payload, frames = frame.data, [frame]
            if pgn in FAST_PGNS:
                complete = self.assembler.feed(frame)
                if complete is None:
                    return event
                payload, frames = complete
            event.frames, event.payload = frames, payload
            try:
                event.decoded = decode_payload(pgn, payload)
                event.status = "decoded" if pgn in PGNS and PGNS[pgn].decoder else "unknown"
            except ValueError as exc:
                event.status = "malformed"
                event.partial = partial_payload(pgn, payload)
                event.errors.append(str(exc))
        except ValueError as exc:
            event.status = "malformed"
            event.errors.append(str(exc))
        finally:
            event.errors.extend(self.assembler.diagnostics)
            self.assembler.diagnostics.clear()
            self.error_count += len(event.errors)
        return event

    def finish(self) -> list[str]:
        try:
            self.assembler.finish()
        except ValueError as exc:
            self.error_count += 1
            return [str(exc)]
        return []
