"""Discovery layouts checked against CANboat; unknown bytes remain available."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import sensors

FAST_PGNS: set[int] = set()


def requested_pgn(data: bytes) -> int:
    if len(data) != 3:
        raise ValueError("PGN requires three bytes")
    value = int.from_bytes(data, "little")
    if value > 0x3FFFF or ((value >> 8 & 255) < 240 and value & 255):
        raise ValueError("invalid PGN bitfields")
    return value


def _length(data: bytes, expected: int) -> None:
    if len(data) != expected:
        raise ValueError(f"expected {expected} payload bytes, got {len(data)}")


def _text(data: bytes) -> str | None:
    text = data.split(b"\x00", 1)[0].split(b"\xff", 1)[0].decode("ascii")
    return text or None


def _decode_discovery(pgn: int, data: bytes) -> dict[str, Any]:
    if pgn == 59904:
        return {"requested_pgn": requested_pgn(data)}
    if pgn == 59392:
        _length(data, 8)
        if data[0] > 3:
            raise ValueError("unknown ISO acknowledgement control")
        return {
            "control": data[0],
            "result": ("ack", "nack", "access_denied", "busy")[data[0]],
            "group_function": data[1],
            "reserved_hex": data[2:5].hex(),
            "requested_pgn": requested_pgn(data[5:]),
        }
    if pgn == 60928:
        _length(data, 8)
        name = int.from_bytes(data, "little")
        fields = [
            ("unique_number", 0, 21),
            ("manufacturer_code", 21, 11),
            ("device_instance_lower", 32, 3),
            ("device_instance_upper", 35, 5),
            ("device_function", 40, 8),
            ("reserved", 48, 1),
            ("device_class", 49, 7),
            ("system_instance", 56, 4),
            ("industry_group", 60, 3),
            ("arbitrary_address_capable", 63, 1),
        ]
        return {
            "name_hex": f"{name:016x}",
            **{field: name >> offset & ((1 << bits) - 1) for field, offset, bits in fields},
        }
    if pgn == 126996:
        _length(data, 134)
        result: dict[str, Any] = {}
        for field, offset in [("nmea_version", 0), ("product_code", 2)]:
            value = int.from_bytes(data[offset : offset + 2], "little")
            result[field] = value if value < 65533 else None
        for field, offset in [
            ("model", 4),
            ("software", 36),
            ("model_version", 68),
            ("serial", 100),
        ]:
            result[field] = _text(data[offset : offset + 32])
        result["certification_level"] = data[132] if data[132] < 253 else None
        result["load_equivalency"] = data[133] if data[133] < 253 else None
        return result
    if pgn == 126464:
        if not data or data[0] not in (0, 1) or (len(data) - 1) % 3:
            raise ValueError("invalid PGN list")
        return {
            "function": ("transmit", "receive")[data[0]],
            "pgns": [requested_pgn(data[i : i + 3]) for i in range(1, len(data), 3)],
        }
    if pgn == 126993:
        _length(data, 8)
        return {
            "interval_ms": sensors.quantity(int.from_bytes(data[:2], "little"), 16),
            "sequence": sensors.quantity(data[2], 8),
            "controller1": data[3] & 3,
            "controller2": data[3] >> 2 & 3,
            "equipment_status": data[3] >> 4 & 3,
            "reserved_hex": data[4:].hex(),
            "reserved_status_bits": data[3] >> 6,
        }
    if pgn == 126998:
        result = {}
        offset = 0
        for field in (
            "installation_description1",
            "installation_description2",
            "manufacturer_information",
        ):
            if offset + 2 > len(data):
                raise ValueError("truncated configuration string")
            length, encoding = data[offset : offset + 2]
            if length < 2 or offset + length > len(data) or encoding not in (0, 1):
                raise ValueError("invalid configuration string")
            result[field] = (
                data[offset + 2 : offset + length]
                .decode("utf-16-le" if encoding == 0 else "ascii")
                .rstrip("\x00")
            )
            offset += length
        if offset != len(data):
            raise ValueError("trailing configuration bytes")
        return result
    return {"unknown_payload_hex": data.hex()}


@dataclass(frozen=True)
class PgnDefinition:
    name: str
    decoder: Callable[[bytes], dict[str, Any]] | None = None
    fast_packet: bool = False


PGNS: dict[int, PgnDefinition] = {}


def register_pgn(pgn: int, definition: PgnDefinition) -> None:
    requested_pgn(pgn.to_bytes(3, "little"))
    PGNS[pgn] = definition
    if definition.fast_packet:
        FAST_PGNS.add(pgn)
    else:
        FAST_PGNS.discard(pgn)


def is_proprietary(pgn: int) -> bool:
    return pgn in (61184, 126720) or 65280 <= pgn <= 65535 or 130816 <= pgn <= 131071


def pgn_name(pgn: int) -> str:
    definition = PGNS.get(pgn)
    return (
        definition.name
        if definition
        else "Proprietary data"
        if is_proprietary(pgn)
        else "Unknown PGN"
    )


def decode_payload(pgn: int, data: bytes) -> dict[str, Any]:
    definition = PGNS.get(pgn)
    if definition is None or definition.decoder is None:
        return {"unknown_payload_hex": data.hex()}
    return definition.decoder(data)


def partial_payload(pgn: int, data: bytes) -> dict[str, Any]:
    # An anomalous single-frame heartbeat still has a known three-byte prefix.
    # Never present this as a complete validated heartbeat or pad its tail.
    if pgn == 126993 and 3 <= len(data) < 8:
        return {
            "interval_ms": sensors.quantity(int.from_bytes(data[:2], "little"), 16),
            "sequence": sensors.quantity(data[2], 8),
        }
    return {}


for _pgn, _name in {
    59392: "ISO Acknowledgement",
    59904: "ISO Request",
    60928: "ISO Address Claim",
    126464: "PGN List",
    126993: "Heartbeat",
    126996: "Product Information",
    126998: "Configuration Information",
}.items():
    register_pgn(
        _pgn,
        PgnDefinition(
            _name,
            lambda data, pgn=_pgn: _decode_discovery(pgn, data),
            _pgn in (126464, 126996, 126998),
        ),
    )
for _pgn, _name, _decoder, _fast in (
    (127488, "Engine Parameters, Rapid Update", sensors.rapid, False),
    (127489, "Engine Parameters, Dynamic", sensors.dynamic, True),
    (127505, "Fluid Level", sensors.fluid, False),
):
    register_pgn(_pgn, PgnDefinition(_name, _decoder, _fast))
for _pgn, _name in {
    60160: "ISO Transport Protocol, Data Transfer",
    60416: "ISO Transport Protocol, Connection Management",
    126208: "NMEA Group Function",
}.items():
    register_pgn(_pgn, PgnDefinition(_name, fast_packet=_pgn == 126208))
