"""Bounded, strictly ordered receive-only fast-packet assembly."""

from dataclasses import dataclass
from decimal import Decimal

from .capture import Frame


@dataclass
class Pending:
    length: int
    data: bytearray
    frames: list[Frame]
    next_index: int = 1


class Reassembler:
    def __init__(self, timeout: Decimal = Decimal("0.75"), capacity: int = 64) -> None:
        if not timeout.is_finite() or timeout <= 0 or capacity < 1:
            raise ValueError("invalid assembly limits")
        self.timeout = timeout
        self.capacity = capacity
        self.diagnostics: list[str] = []
        self.pending: dict[tuple[str, int, int, int | None, int], Pending] = {}

    def feed(self, frame: Frame) -> tuple[bytes, list[Frame]] | None:
        ident = frame.identifier
        if not 2 <= len(frame.data) <= 8:
            for key in list(self.pending):
                if key[:4] == (frame.channel, ident.source, ident.pgn, ident.destination):
                    del self.pending[key]
            raise ValueError("invalid fast-packet frame length")
        sequence, index = frame.data[0] >> 5, frame.data[0] & 31
        key = (frame.channel, ident.source, ident.pgn, ident.destination, sequence)
        packet = self.pending.get(key)
        if index == 0:
            length = frame.data[1]
            if not 1 <= length <= 223 or len(frame.data) < 2 + min(6, length):
                raise ValueError("invalid fast-packet declared length")
            # Expire old streams on timestamped captures, never invent timestamps.
            for old_key, old in list(self.pending.items()):
                start = old.frames[0].timestamp
                if (
                    frame.timestamp is not None
                    and start is not None
                    and frame.timestamp - start >= self.timeout
                ):
                    del self.pending[old_key]
                    self.diagnostics.append("expired incomplete fast packet")
            if key in self.pending:
                del self.pending[key]
                raise ValueError("duplicate fast-packet start")
            if len(self.pending) >= self.capacity:
                raise ValueError("fast-packet capacity exceeded")
            packet = Pending(length, bytearray(frame.data[2 : 2 + min(6, length)]), [frame])
            self.pending[key] = packet
        else:
            if packet is None:
                raise ValueError("orphan fast-packet continuation")
            start = packet.frames[0].timestamp
            previous = packet.frames[-1].timestamp
            if (
                frame.timestamp is not None
                and start is not None
                and (
                    frame.timestamp - start >= self.timeout
                    or (previous is not None and frame.timestamp < previous)
                )
            ):
                del self.pending[key]
                raise ValueError("fast-packet timeout or reversed timestamp")
            remaining = packet.length - len(packet.data)
            if index != packet.next_index or len(frame.data) < 1 + min(7, remaining):
                del self.pending[key]
                raise ValueError("fast-packet ordering or length error")
            packet.data.extend(frame.data[1 : 1 + min(7, remaining)])
            packet.frames.append(frame)
            packet.next_index += 1
        if len(packet.data) == packet.length:
            del self.pending[key]
            return bytes(packet.data), packet.frames
        return None

    def finish(self) -> None:
        if self.pending:
            count = len(self.pending)
            self.pending.clear()
            raise ValueError(f"{count} incomplete fast packet(s) at EOF")
