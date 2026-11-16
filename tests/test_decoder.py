from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from siren_mapper.capture import decode_identifier, parse_line
from siren_mapper.cli import decode_file, main
from siren_mapper.protocol import decode_payload, requested_pgn
from siren_mapper.transport import Reassembler

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def frames():
    return [
        parse_line(line, i)
        for i, line in enumerate((FIXTURES / "product.candump").read_text().splitlines(), 1)
    ]


def test_product_evidence():
    records, errors = decode_file(FIXTURES / "product.candump")
    assert not errors
    assert len(records) == 1
    assert len(records[0]["frames"]) == 20
    assert records[0]["decoded"] == {
        "nmea_version": 3100,
        "product_code": 13939,
        "model": "Siren 3 Pro",
        "software": "1.0.0",
        "model_version": None,
        "serial": "21003942",
        "certification_level": 2,
        "load_equivalency": 1,
    }


def test_lists():
    records, errors = decode_file(FIXTURES / "pgn-lists.candump")
    assert not errors
    assert [r["decoded"] for r in records] == [
        {
            "function": "transmit",
            "pgns": [59392, 59904, 60160, 60416, 60928, 126208, 126464, 126993, 126996],
        },
        {
            "function": "receive",
            "pgns": [59392, 59904, 60160, 60416, 60928, 65240, 126208, 127488, 127489, 127505],
        },
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        (0x18EA0523, (6, 59904, 35, 5)),
        (0x1DF01105, (7, 126993, 5, None)),
        (0x19EEFF05, (6, 126464, 5, 255)),
        (0x01EEFF05, (0, 126464, 5, 255)),
        (0x03F01105, (0, 258065, 5, None)),
    ],
)
def test_identifiers(value, expected):
    ident = decode_identifier(value)
    assert (ident.priority, ident.pgn, ident.source, ident.destination) == expected


@pytest.mark.parametrize("value", [-1, 0x20000000])
def test_identifier_limits(value):
    with pytest.raises(ValueError):
        decode_identifier(value)


@pytest.mark.parametrize(
    "line",
    [
        "(1760000000.000001) can1 18EA0523#14F001 R",
        "(1760000000.000001) can1 T 18EA0523#14F001",
        "(1760000000.000001) can1 18EA0523 [3] 14 F0 01 R",
    ],
)
def test_candump_formats(line):
    frame = parse_line(line)
    assert frame.data == bytes.fromhex("14F001")
    assert frame.timestamp == Decimal("1760000000.000001")
    assert frame.direction in ("T", "R")
    assert frame.raw == line


@pytest.mark.parametrize(
    "line",
    [
        "can1 123#01",
        "can1 20000000#01",
        "can1 18EA0523#1",
        "can1 18EA0523##114F001",
        "can1 18EA0523#R8",
        "can1 18EA0523 [3] 14 F0",
        "can1 18EA0523#000000000000000000",
        "can1 T R 18EA0523#14F001",
    ],
)
def test_invalid_lines(line):
    with pytest.raises(ValueError):
        parse_line(line)


def test_discovery_and_anomaly():
    records, errors = decode_file(FIXTURES / "discovery.candump")
    assert len(errors) == 1
    assert records[0]["decoded"] == {"requested_pgn": 126996}
    assert records[2]["decoded"]["result"] == "nack"
    assert records[2]["decoded"]["requested_pgn"] == 126998
    assert records[3]["decoded"]["name_hex"] == "c08cab00815fffff"
    assert records[4]["decoded"]["name_hex"] == "c0648c00ffd89fe6"
    assert records[5]["status"] == "malformed"
    assert records[5]["payload_hex"] == "60eae10c010000"


@pytest.mark.parametrize(
    "pgn,data",
    [
        (59904, b"\x14\xf0"),
        (59392, bytes(7)),
        (59392, b"\x04" + bytes(7)),
        (60928, bytes(9)),
        (126996, bytes(133)),
        (126464, b"\x02"),
        (126464, b"\x00\x01"),
        (126998, b"\x01\x01"),
        (126998, b"\x02\x02"),
        (126998, b"\x02\x01" * 3 + b"\x00"),
    ],
)
def test_malformed_payloads(pgn, data):
    with pytest.raises(ValueError):
        decode_payload(pgn, data)


def test_configuration_and_heartbeat():
    assert decode_payload(126998, b"\x03\x01a\x04\x00b\x00\x02\x01") == {
        "installation_description1": "a",
        "installation_description2": "b",
        "manufacturer_information": "",
    }
    heartbeat = decode_payload(126993, bytes.fromhex("60EA01CCFFFFFFFF"))
    assert heartbeat["interval_ms"] == 60000
    assert heartbeat["sequence"] == 1
    assert heartbeat["controller1"] == 0
    assert heartbeat["controller2"] == 3


@pytest.mark.parametrize("data", [b"\xff\xff\xff", b"\x01\xea\x00", b"\x00\x00\x04"])
def test_invalid_requested_pgn(data):
    with pytest.raises(ValueError):
        requested_pgn(data)


def test_missing_duplicate_order_and_eof():
    data = frames()
    for index in (0, 3):
        assembler = Reassembler()
        assembler.feed(data[0])
        with pytest.raises(ValueError):
            assembler.feed(data[index])
    assembler = Reassembler()
    assembler.feed(data[0])
    assembler.feed(data[1])
    with pytest.raises(ValueError, match="ordering"):
        assembler.feed(data[1])
    assembler.feed(data[0])
    with pytest.raises(ValueError, match="incomplete"):
        assembler.finish()
    with pytest.raises(ValueError, match="orphan"):
        Reassembler().feed(data[1])


@pytest.mark.parametrize("stamp", ["0.75", "-0.1"])
def test_time_window(stamp):
    data = frames()
    assembler = Reassembler()
    assembler.feed(replace(data[0], timestamp=Decimal(0)))
    with pytest.raises(ValueError, match="timestamp|timeout"):
        assembler.feed(replace(data[1], timestamp=Decimal(stamp)))


def test_interleaving_and_isolation():
    data = frames()
    assembler = Reassembler()
    completed = []
    for frame in data:
        for other in (
            frame,
            replace(frame, can_id=frame.can_id + 1),
            replace(frame, channel="other"),
            replace(frame, data=bytes([frame.data[0] | 0xE0]) + frame.data[1:]),
        ):
            result = assembler.feed(other)
            if result:
                completed.append(result)
    assert len(completed) == 4
    assert len({payload for payload, _ in completed}) == 1
    assembler.finish()


@pytest.mark.parametrize("payload", [b"\x00\x00", b"\x00\xe0", b"\x00\x86a"])
def test_invalid_first_frame(payload):
    with pytest.raises(ValueError):
        Reassembler().feed(replace(frames()[0], data=payload))


def test_short_final_and_truncation():
    data = frames()
    assembler = Reassembler()
    for frame in data[:-1]:
        assembler.feed(frame)
    assert len(assembler.feed(replace(data[-1], data=data[-1].data[:3]))[0]) == 134
    assembler = Reassembler()
    for frame in data[:-1]:
        assembler.feed(frame)
    with pytest.raises(ValueError):
        assembler.feed(replace(data[-1], data=data[-1].data[:2]))


def test_cli_and_immutable_input(capsys, tmp_path):
    path = tmp_path / "input.log"
    original = (FIXTURES / "product.candump").read_bytes()
    path.write_bytes(original)
    assert main(["decode", str(path)]) == 0
    first = capsys.readouterr().out
    assert main(["decode", str(path)]) == 0
    assert capsys.readouterr().out == first
    assert path.read_bytes() == original
    assert main(["decode", str(FIXTURES / "discovery.candump")]) == 1
    assert main(["decode", str(tmp_path / "absent")]) == 2


def test_expired_stream_does_not_discard_fresh_start():
    data = frames()
    assembler = Reassembler()
    assembler.feed(replace(data[0], timestamp=Decimal(0)))
    fresh = replace(data[0], channel="other", timestamp=Decimal(1))
    assert assembler.feed(fresh) is None
    assert assembler.diagnostics == ["expired incomplete fast packet"]
    result = None
    for frame in data[1:]:
        result = assembler.feed(replace(frame, channel="other", timestamp=Decimal("1.1")))
    assert result is not None
    assembler.finish()


def test_destination_isolation_and_sequence_reuse():
    assembler = Reassembler()
    # Two one-continuation PGN lists addressed to distinct destinations.
    for dest in (5, 35):
        assert assembler.feed(parse_line(f"can1 19EE{dest:02X}05#00070000E80000EA")) is None
    for dest in (5, 35):
        result = assembler.feed(parse_line(f"can1 19EE{dest:02X}05#0100"))
        assert result[0] == bytes.fromhex("0000E80000EA00")
    assembler.finish()
    for _ in range(2):
        assert assembler.feed(parse_line("can1 19EEFF05#000100"))[0] == b"\x00"


def test_capacity_and_bad_frame_isolation():
    data = frames()
    assembler = Reassembler(capacity=1)
    assembler.feed(data[0])
    with pytest.raises(ValueError, match="capacity"):
        assembler.feed(replace(data[0], channel="other"))
    with pytest.raises(ValueError, match="length"):
        assembler.feed(replace(data[0], channel="other", data=b""))
    for frame in data[1:]:
        result = assembler.feed(frame)
    assert result is not None


def test_cli_default_and_decode_never_open_socket(monkeypatch, capsys):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("offline CLI attempted socket access")

    monkeypatch.setattr(socket, "socket", forbidden)
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert main(["decode", str(FIXTURES / "product.candump")]) == 0


@pytest.mark.parametrize("length", [1, 6, 7, 8, 9, 222, 223])
def test_fast_packet_length_boundaries(length):
    payload = bytes(range(length))
    parts = [bytes([0xE0, length]) + payload[:6]]
    parts.extend(
        bytes([0xE0 | index]) + payload[offset : offset + 7]
        for index, offset in enumerate(range(6, length, 7), 1)
    )
    assembler = Reassembler()
    result = None
    for part in parts:
        result = assembler.feed(replace(frames()[0], data=part.ljust(8, b"\xff")))
    assert result[0] == payload
    assembler.finish()


def test_timed_out_sequence_can_start_again():
    assembler = Reassembler()
    data = frames()
    assembler.feed(replace(data[0], timestamp=Decimal(0)))
    for frame in data:
        result = assembler.feed(replace(frame, timestamp=Decimal(1)))
    assert result is not None
    assert assembler.diagnostics == ["expired incomplete fast packet"]
