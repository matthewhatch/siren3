"""Strict classical extended-CAN candump reader; raw lines remain evidence."""

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Identifier:
    priority: int
    pgn: int
    source: int
    destination: int | None


def decode_identifier(value: int) -> Identifier:
    if not 0 <= value <= 0x1FFFFFFF:
        raise ValueError("identifier must fit 29 bits")
    pgn = value >> 8 & 0x3FFFF
    addressed = (value >> 16 & 255) < 240
    return Identifier(
        value >> 26,
        pgn & ~255 if addressed else pgn,
        value & 255,
        value >> 8 & 255 if addressed else None,
    )


@dataclass(frozen=True)
class Frame:
    channel: str
    can_id: int
    data: bytes
    timestamp: Decimal | None = None
    direction: str | None = None
    raw: str = ""
    line_number: int = 0

    @property
    def identifier(self) -> Identifier:
        return decode_identifier(self.can_id)


_PREFIX = re.compile(r"^(?:\((\d+(?:\.\d+)?)\)\s+)?(\S+)\s+(.*)$")
_COMPACT = re.compile(r"([0-9A-Fa-f]{8})#([0-9A-Fa-f]*)")
_VERBOSE = re.compile(r"([0-9A-Fa-f]{8})\s+\[([0-8])\](?:\s+(.*))?")


def parse_line(line: str, line_number: int = 0) -> Frame | None:
    raw = line.rstrip("\r\n")
    if not raw.strip() or raw.lstrip().startswith("#"):
        return None
    match = _PREFIX.fullmatch(raw.strip())
    if not match:
        raise ValueError("invalid candump prefix")
    stamp, channel, body = match.groups()
    tokens = body.split()
    flags = [token for token in tokens if token in ("T", "R")]
    if len(flags) > 1:
        raise ValueError("multiple direction flags")
    body = " ".join(token for token in tokens if token not in ("T", "R"))
    compact = _COMPACT.fullmatch(body)
    verbose = _VERBOSE.fullmatch(body)
    if compact:
        identifier, payload = compact.groups()
        if len(payload) % 2 or len(payload) > 16:
            raise ValueError("invalid classical CAN payload")
    elif verbose:
        identifier, dlc, payload = verbose.groups()
        octets = (payload or "").split()
        if len(octets) != int(dlc) or any(not re.fullmatch(r"[\da-fA-F]{2}", x) for x in octets):
            raise ValueError("DLC/payload mismatch")
        payload = "".join(octets)
    else:
        raise ValueError("expected extended classical CAN data frame")
    can_id = int(identifier, 16)
    decode_identifier(can_id)
    return Frame(
        channel,
        can_id,
        bytes.fromhex(payload),
        Decimal(stamp) if stamp else None,
        flags[0] if flags else None,
        raw,
        line_number,
    )
