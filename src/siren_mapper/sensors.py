"""Read-only PGN layouts checked against CANboat and reviewed simulator codecs."""

import struct
from typing import Any

STATUS1 = (
    "check engine",
    "over temperature",
    "low oil pressure",
    "low oil level",
    "low fuel pressure",
    "low system voltage",
    "low coolant level",
    "water flow",
    "water in fuel",
    "charge indicator",
    "preheat indicator",
    "high boost pressure",
    "rev limit exceeded",
    "EGR system",
    "throttle position sensor",
    "emergency stop",
)
STATUS2 = (
    "warning level 1",
    "warning level 2",
    "power reduction",
    "maintenance needed",
    "engine communication error",
    "secondary throttle",
    "neutral start protect",
    "engine shutting down",
)
TANK_TYPES = {
    0: "fuel",
    1: "fresh water",
    2: "gray water",
    3: "live well",
    4: "oil",
    5: "black water",
}


def quantity(raw: int, bits: int, scale: float = 1, *, signed: bool = False) -> float | int | None:
    maximum = (1 << (bits - int(signed))) - 1
    reserved = 2 if bits <= 4 else 3
    return round(raw * scale, 8) if raw <= maximum - reserved else None


def rapid(data: bytes) -> dict[str, Any]:
    if len(data) != 8:
        raise ValueError("PGN 127488 requires exactly eight bytes")
    instance, rpm, boost, trim = struct.unpack("<BHHb", data[:6])
    return {
        "engine_instance": quantity(instance, 8),
        "rpm": quantity(rpm, 16, 0.25),
        "boost_pressure_pa": quantity(boost, 16, 100),
        "trim_pct": quantity(trim, 8, signed=True),
        "reserved_hex": data[6:].hex(),
    }


def dynamic(data: bytes) -> dict[str, Any]:
    if len(data) != 26:
        raise ValueError("PGN 127489 requires exactly 26 bytes")
    raw = struct.unpack("<BHHHhhIHHBHHbb", data)
    specs = [
        ("engine_instance", 8, 1, False),
        ("oil_pressure_pa", 16, 100, False),
        ("oil_temperature_k", 16, 0.1, False),
        ("coolant_temperature_k", 16, 0.01, False),
        ("alternator_voltage_v", 16, 0.01, True),
        ("fuel_rate_lph", 16, 0.1, True),
        ("engine_time_s", 32, 1, False),
        ("coolant_pressure_pa", 16, 100, False),
        ("fuel_pressure_pa", 16, 1000, False),
    ]
    result = {
        name: quantity(value, bits, scale, signed=signed)
        for value, (name, bits, scale, signed) in zip(raw, specs)
    }
    result.update(
        reserved_hex=data[19:20].hex(),
        status1=raw[10],
        status2=raw[11],
        status2_reserved_bits=raw[11] & 0xFF00,
        reported_status_flags=[
            name
            for word, names in ((raw[10], STATUS1), (raw[11], STATUS2))
            for bit, name in enumerate(names)
            if word & (1 << bit)
        ],
        engine_load_pct=quantity(raw[12], 8, signed=True),
        engine_torque_pct=quantity(raw[13], 8, signed=True),
    )
    return result


def fluid(data: bytes) -> dict[str, Any]:
    if len(data) != 8:
        raise ValueError("PGN 127505 requires exactly eight bytes")
    instance, level, capacity = struct.unpack("<BhI", data[:7])
    kind = instance >> 4
    return {
        "tank_instance": quantity(instance & 15, 4),
        "fluid_type_code": kind,
        "fluid_type": TANK_TYPES.get(
            kind, "unavailable" if kind == 15 else "error" if kind == 14 else "unknown enum"
        ),
        "level_pct": quantity(level, 16, 0.004, signed=True),
        "capacity_l": quantity(capacity, 32, 0.1),
        "reserved_hex": data[7:].hex(),
    }
