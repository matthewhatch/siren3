"""Human contract and shared pipeline tests; no live interface by default."""

import io
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from siren_mapper import live
from siren_mapper.capture import parse_line
from siren_mapper.cli import main
from siren_mapper.decoder import PacketDecoder
from siren_mapper.evidence import read_capture
from siren_mapper.interpreter import HumanInterpreter, replay
from siren_mapper.protocol import FAST_PGNS, PGNS, PgnDefinition, decode_payload, register_pgn

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def human_fixture(name, **options):
    output = io.StringIO()
    status = replay(FIXTURES / name, output, **options)
    return status, output.getvalue()


def test_real_product_one_completed_summary_and_pending_fragments():
    status, text = human_fixture("product.candump")
    assert status == 0
    assert text.count("[fast packet complete:") == 1
    assert text.count("pending; no decoded fields yet") == 19
    assert text.count('Model="Siren 3 Pro"') == 1
    assert 'Software="1.0.0"' in text
    assert 'Serial="21003942"' in text
    assert "priority=6 src=5 dst=ALL PGN=126996 Product Information len=8" in text
    assert "timestamp=?" in text and "direction=?" in text


def test_real_lists_roles_and_negative_ack_and_heartbeat_anomaly():
    status, text = human_fixture("pgn-lists.candump")
    assert status == 0
    assert text.count("Advertised transmit PGN list") == 1
    assert text.count("Advertised receive PGN list") == 1
    assert "127488, 127489, 127505" in text
    status, text = human_fixture("discovery.candump")
    assert status == 1
    assert "Negative ACK for Configuration Information (PGN 126998)" in text
    assert "Malformed packet; expected 8 payload bytes, got 7" in text
    assert "known prefix only" in text
    assert "Interval=60000 ms (60 s); Sequence=225" in text
    assert 'ISO NAME="c08cab00815fffff"' in text


def test_synthetic_heartbeat_rpm_fluid_dynamic_and_unknown():
    status, text = human_fixture("interpreter-synthetic.candump")
    assert status == 0
    assert "Sequence=224" in text and "Sequence=225" in text
    assert "Interval=60000 ms (60 s)" in text
    assert "Engine instance=0; Engine speed=1200 RPM" in text
    assert "Tilt/trim=-4 %" in text
    assert "Boost pressure=unavailable/invalid" in text
    assert "Oil pressure=150000 Pa" in text
    assert "Oil temperature=310 K" in text
    assert "Coolant temperature=300.15 K" in text
    assert "Alternator voltage=-2 V" in text
    assert "Fuel rate=12.3 L/h" in text
    assert "Engine running time=3600 s" in text
    assert "Engine torque=-20 %" in text
    assert "over temperature, low oil pressure, maintenance needed" in text
    assert "Fluid level=50 %; Tank capacity=1000 L" in text
    assert "Proprietary payload; vendor meaning unknown" in text
    assert "HELLO" not in text
    assert "ISO Transport Protocol, Data Transfer" in text
    assert "Payload layout unsupported/unknown" in text
    assert "TX/local (origin unverified)" in text


def test_verbose_raw_separate_and_safe_strings():
    _, plain = human_fixture("product.candump")
    _, raw = human_fixture("product.candump", raw=True)
    _, verbose = human_fixture("product.candump", verbose=True)
    assert "CAN ID=" not in plain and "assembled=" not in plain
    assert "CAN ID=19F01405" in raw and "assembled=1C0C" in raw
    assert '\n  Model="Siren 3 Pro"' in verbose
    assert "Capture lines=1, 2, 3" in verbose
    _, verbose = human_fixture("interpreter-synthetic.candump", verbose=True)
    assert 'Reserved hex="ffff"' in verbose
    assert "Controller 2=unavailable (3)" in verbose
    assert "Equipment status=operational (0)" in verbose
    # Text fields cannot inject terminal controls/new lines.
    data = b"\x03\x01\x1b\x02\x01\x02\x01"
    decoded = decode_payload(126998, data)
    assert decoded["installation_description1"] == "\x1b"


@pytest.mark.parametrize("raw", [65533, 65534, 65535])
def test_rapid_unavailable_error_reserved_values(raw):
    data = struct.pack("<BHHb", 255, raw, 65535, 127) + b"\xff\xff"
    decoded = decode_payload(127488, data)
    assert decoded["engine_instance"] is None
    assert decoded["rpm"] is None and decoded["boost_pressure_pa"] is None
    assert decoded["trim_pct"] is None
    assert decoded["reserved_hex"] == "ffff"


def test_dynamic_signed_sentinels_reserved_bits_and_fluid_signedness():
    data = struct.pack(
        "<BHHHhhIHHBHHbb",
        255,
        65535,
        65535,
        65535,
        32767,
        32766,
        4294967295,
        65535,
        65535,
        0xAA,
        0x8000,
        0xFF08,
        127,
        126,
    )
    decoded = decode_payload(127489, data)
    assert decoded["alternator_voltage_v"] is None and decoded["fuel_rate_lph"] is None
    assert decoded["engine_load_pct"] is None and decoded["engine_torque_pct"] is None
    assert decoded["status2_reserved_bits"] == 0xFF00
    assert decoded["reported_status_flags"] == ["emergency stop", "maintenance needed"]
    assert decoded["reserved_hex"] == "aa"
    fluid = decode_payload(127505, struct.pack("<BhI", 0x62, -6250, 4294967295) + b"\xaa")
    assert fluid["level_pct"] == -25
    assert fluid["fluid_type"] == "unknown enum"
    assert fluid["capacity_l"] is None and fluid["reserved_hex"] == "aa"


@pytest.mark.parametrize("pgn,length", [(127488, 7), (127489, 25), (127505, 7)])
def test_sensor_lengths(pgn, length):
    with pytest.raises(ValueError):
        decode_payload(pgn, bytes(length))


def test_errors_missing_frames_and_recovery_do_not_crash(tmp_path):
    product = (FIXTURES / "product.candump").read_text().splitlines()
    file = tmp_path / "damaged.log"
    file.write_text(
        product[0]
        + "\n"
        + product[2]
        + "\n"
        + "bad frame\n"
        + "can1 18EA0523#14\ncan1 18EA0523#14F001\n"
        + product[0]
        + "\n"
    )
    output = io.StringIO()
    assert replay(file, output) == 1
    text = output.getvalue()
    assert "ordering or length error" in text
    assert "malformed CAN frame" in text
    assert "Request Product Information" in text  # continues after errors
    assert "Incomplete session: 1 incomplete fast packet(s) at EOF" in text


def test_timeout_and_timestamp_order_preserved(tmp_path):
    file = tmp_path / "time.log"
    file.write_text(
        "(1.000000) can1 19F01405#00861C0C73365369 R\n"
        "(2.000000) can1 19F01405#0172656E20332050 R\n"
        "(0.500000) can1 18EA0523#14F001 T\n"
    )
    output = io.StringIO()
    assert replay(file, output) == 1
    text = output.getvalue()
    assert "timeout or reversed timestamp" in text
    assert text.index("2.000000") < text.index("0.500000")


def test_pgn_registration_shared_with_evidence_and_human(tmp_path):
    pgn = 130100
    file = tmp_path / "extension.log"
    identifier = (6 << 26) | (pgn << 8) | 5
    file.write_text(f"can1 {identifier:08X}#07\n")
    try:
        register_pgn(pgn, PgnDefinition("Test extension", lambda b: {"example": b[0]}))
        output = io.StringIO()
        assert replay(file, output) == 0
        assert "Test extension" in output.getvalue() and "Example=7" in output.getvalue()
        assert read_capture(file)["records"][0]["decoded"] == {"example": 7}
    finally:
        PGNS.pop(pgn)
        FAST_PGNS.discard(pgn)


def test_shared_event_and_evidence_agree_on_sensor_fields():
    capture = read_capture(FIXTURES / "interpreter-synthetic.candump")
    decoder = PacketDecoder()
    decoded = []
    for number, line in enumerate(
        (FIXTURES / "interpreter-synthetic.candump").read_text().splitlines(), 1
    ):
        frame = parse_line(line, number)
        if frame is not None:
            event = decoder.feed(frame)
            if event.payload is not None:
                decoded.append(event.decoded)
    assert decoded == [record.get("decoded") for record in capture["records"]]
    assert decoder.finish() == []


@pytest.mark.parametrize(
    "verbose,raw", [(False, False), (False, True), (True, False), (True, True)]
)
def test_live_capture_and_offline_replay_identical(monkeypatch, tmp_path, verbose, raw):
    # Timed variants use synthetic directions/timestamps while retaining historical
    # product/list bytes. The original fixtures are never rewritten.
    original = []
    for name in ("product.candump", "pgn-lists.candump", "discovery.candump"):
        original += [line for line in (FIXTURES / name).read_text().splitlines() if line]
    original.append("can1 18EA0523#14F001")  # valid frame after the heartbeat anomaly
    messages = []
    for index, line in enumerate(original):
        frame = parse_line(line)
        messages.append(
            SimpleNamespace(
                arbitration_id=frame.can_id,
                is_extended_id=True,
                data=frame.data,
                timestamp=1760000000 + index * 0.001,
                is_rx=index % 2 == 0,
                is_error_frame=False,
                is_remote_frame=False,
                is_fd=False,
            )
        )
    bus = SimpleNamespace(
        recv=lambda timeout: messages.pop(0),
        shutdown=lambda: None,
        send=lambda *a, **k: pytest.fail("interpreter transmitted"),
    )
    monkeypatch.setattr(live, "inspect_interface", lambda *a: {"linkinfo": {"info_kind": "vcan"}})
    monkeypatch.setattr(live, "_open_receiver", lambda *a: bus)
    output = tmp_path / "live.log"
    displayed = io.StringIO()
    view = HumanInterpreter(displayed, verbose=verbose, raw=raw)
    live.capture("can1", output, listen_only=True, count=len(messages), on_frame=view.feed_line)
    status = view.finish()
    replayed = io.StringIO()
    assert (
        replay(output, replayed, verbose=verbose, raw=raw) == status == 1
    )  # known 7-byte heartbeat
    assert replayed.getvalue() == displayed.getvalue()


@pytest.mark.parametrize(
    "args", [["decode", "--human"], ["decode", "--raw"], ["decode", "--verbose"], ["replay"]]
)
def test_cli_human_offline_no_socket(args, monkeypatch, capsys):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("socket opened"))
    assert main([args[0], str(FIXTURES / "product.candump"), *args[1:]]) == 0
    assert 'Model="Siren 3 Pro"' in capsys.readouterr().out


def test_replay_speed_and_negative_gaps(monkeypatch, tmp_path):
    file = tmp_path / "paced.log"
    file.write_text(
        "(1.000000) can1 18EA0523#14F001 R\n"
        "(1.400000) can1 18EA0523#14F001 R\n"
        "(1.200000) can1 18EA0523#14F001 R\n"
    )
    waits = []
    monkeypatch.setattr("siren_mapper.interpreter.time.sleep", waits.append)
    assert replay(file, io.StringIO(), speed=2) == 0
    assert sum(waits) == pytest.approx(0.2)
    assert all(0 <= wait <= 0.1 for wait in waits)
    for bad in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            replay(file, io.StringIO(), speed=bad)


def test_cli_json_decode_still_default(capsys):
    assert main(["decode", str(FIXTURES / "interpreter-synthetic.candump")]) == 0
    assert json.loads(capsys.readouterr().out)["records"][2]["decoded"]["rpm"] == 1200


def test_cli_capture_human_keeps_summary_on_stderr(monkeypatch, tmp_path, capsys):
    messages = [
        SimpleNamespace(
            arbitration_id=0x18EA0523,
            is_extended_id=True,
            data=bytes.fromhex("14F001"),
            timestamp=1.0,
            is_rx=True,
            is_error_frame=False,
            is_remote_frame=False,
            is_fd=False,
        )
    ]
    bus = SimpleNamespace(recv=lambda timeout: messages.pop(0), shutdown=lambda: None)
    monkeypatch.setattr(live, "inspect_interface", lambda *a: {"linkinfo": {"info_kind": "vcan"}})
    monkeypatch.setattr(live, "_open_receiver", lambda *a: bus)
    assert (
        main(
            [
                "capture",
                "--channel",
                "vcan0",
                "--listen-only",
                "--count",
                "1",
                "--output",
                str(tmp_path / "cli.log"),
                "--human",
            ]
        )
        == 0
    )
    text = capsys.readouterr()
    assert "ISO Request" in text.out
    assert '"stop_reason": "count"' in text.err
    assert '"stop_reason"' not in text.out


def test_untrusted_text_does_not_emit_terminal_controls(tmp_path):
    file = tmp_path / "escape.log"
    file.write_text("\x1b[31m 18EA0523#14F001\n")
    output = io.StringIO()
    assert replay(file, output, raw=True) == 0
    assert "\x1b" not in output.getvalue()
    assert "\\u001b" in output.getvalue()
