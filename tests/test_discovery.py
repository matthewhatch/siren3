"""Offline active-discovery acceptance and failure-path tests; no CAN sockets."""

import hashlib
import io
import json
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from siren_mapper import discovery
from siren_mapper.cli import main
from siren_mapper.discovery import Options, Session
from siren_mapper.simulator_adapter import AddressManager


@pytest.fixture
def protocol():
    pytest.importorskip("can")
    return pytest.importorskip("nmea_sim.device_protocol")


def opts(tmp_path, **changes):
    return Options("vcan0", tmp_path / "active.log", 5, 123456, allow_transmit=True, **changes)


class Bus:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, message, timeout):
        assert timeout <= 0.1
        self.sent.append(message)

    def shutdown(self):
        self.closed = True


def session(tmp_path, **changes):
    options = opts(tmp_path, **changes)
    manager = AddressManager(
        options.manufacturer_code, options.unique_number, options.source_address
    )
    return Session(options, Bus(), manager, io.BytesIO(), 0, [], clock=lambda: 1.0)


def message(identifier, payload, stamp=1.3, **changes):
    import can

    return can.Message(
        arbitration_id=identifier,
        data=payload,
        is_extended_id=True,
        timestamp=stamp,
        check=True,
        **changes,
    )


def acquired(run):
    run.advance(1.0)
    assert run.bus.sent[0].arbitration_id == 0x18EEFF23
    assert len(run.bus.sent) == 1
    run.advance(1.249)
    assert len(run.bus.sent) == 1
    run.advance(1.25)
    assert run.requests[0]["pgn"] == 126996
    assert run.manager.owns_address


@pytest.mark.parametrize(
    "changes",
    [
        {"allow_transmit": False},
        {"target": 255},
        {"source_address": 254},
        {"source_address": 5},
        {"unique_number": 0},
        {"manufacturer_code": 2047},
        {"baseline": 0},
        {"response_window": float("nan")},
        {"interval": 0.1},
        {"recovery": float("inf")},
        {"duration": 61},
    ],
)
def test_options_fail_before_interface(monkeypatch, tmp_path, changes):
    options = replace(opts(tmp_path), **changes)
    monkeypatch.setattr(
        discovery, "inspect_interface", lambda *a, **k: pytest.fail("link inspected")
    )
    with pytest.raises(ValueError):
        discovery.discover(options)
    assert not list(tmp_path.iterdir())


def test_physical_identity_and_mode_gates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        discovery, "inspect_interface", lambda *a, **k: {"linkinfo": {"info_kind": "can"}}
    )
    with pytest.raises(ValueError, match="hardware"):
        discovery.preflight(opts(tmp_path))
    with pytest.raises(ValueError, match="virtual-only"):
        discovery.preflight(opts(tmp_path, hardware=True))
    assert discovery.preflight(opts(tmp_path, hardware=True, manufacturer_code=123))
    monkeypatch.setattr(
        discovery, "inspect_interface", lambda *a, **k: {"linkinfo": {"info_kind": "vcan"}}
    )
    with pytest.raises(ValueError, match="verified physical"):
        discovery.preflight(opts(tmp_path, hardware=True))


def test_claim_before_three_requests_and_timeout_bounds(tmp_path, protocol):
    run = session(tmp_path)
    run.advance(0.99)
    assert not run.bus.sent
    acquired(run)
    run.advance(3.25)
    run.advance(5.25)
    run.advance(7.25)
    assert run.advance(8.25)
    requests = [m for m in run.bus.sent if (m.arbitration_id >> 16 & 255) == 234]
    assert [bytes(m.data) for m in requests] == [
        bytes.fromhex(x) for x in ("14F001", "16F001", "00EE01")
    ]
    assert all(m.arbitration_id == 0x18EA0523 for m in requests)
    assert len(run.bus.sent) == 4  # one claim, three requests; no simulator heartbeat or telemetry
    assert [r["status"] for r in run.requests] == ["timeout"] * 3
    assert all(
        b["sent_elapsed_seconds"] - a["sent_elapsed_seconds"] >= run.options.interval
        for a, b in zip(run.requests, run.requests[1:])
    )


def test_baseline_occupied_address_and_target_exclusion(tmp_path, protocol):
    run = session(tmp_path)
    run.receive(message(0x18EEFF23, b"\x00" * 8), 0.2)
    assert not run.bus.sent
    run.advance(1)
    assert run.manager.address != 35
    assert run.manager.address != 5
    assert len(run.bus.sent) == 1


def test_collision_loses_relocates_and_pauses_requests(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    # A lower NAME wins, even if the frame originated from another local socket.
    run.receive(message(0x18EEFF23, b"\x00" * 8, is_rx=False), 1.3)
    assert run.manager.address == 36
    assert not run.manager.owns_address
    assert run.requests[0]["status"] == "inconclusive_address_change"
    run.advance(1.54)
    assert len(run.requests) == 1
    run.advance(2.3)
    assert run.manager.owns_address
    assert run.requests[1]["source"] == 36


def test_winning_collision_reasserts_and_waits(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    run.receive(message(0x18EEFF23, b"\xff" * 8), 1.3)
    assert run.manager.address == 35
    assert not run.manager.owns_address
    assert run.requests[0]["status"] == "inconclusive_address_change"
    run.advance(1.54)
    assert not run.manager.owns_address
    run.advance(1.55)
    assert run.manager.owns_address


def test_duplicate_name_and_exhaustion_stop(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    with pytest.raises(ValueError, match="duplicate"):
        run.receive(message(0x18EEFF24, run.manager.name.to_bytes(8, "little")), 1.3)
    run = session(tmp_path)
    for address in range(252):
        run.manager.reserve(address)
    with pytest.raises(ValueError, match="no unoccupied"):
        run.advance(1)
    assert not run.bus.sent


def test_target_name_changes_stop(tmp_path, protocol):
    run = session(tmp_path)
    run.receive(message(0x18EEFF05, b"\x00" * 8), 0.1)
    with pytest.raises(ValueError, match="target address NAME changed"):
        run.receive(message(0x18EEFF05, b"\x01" * 8), 0.2)
    assert not run.bus.sent


def test_product_reassembly_nack_and_both_lists(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    lines = (Path(__file__).parents[1] / "fixtures/product.candump").read_text().splitlines()
    for index, line in enumerate(lines):
        payload = bytes.fromhex(line.split("#")[1])
        run.receive(message(0x19F01405, payload, stamp=1.3 + index * 0.001), 1.3 + index * 0.001)
    assert run.requests[0]["observations"][0]["decoded"]["model"] == "Siren 3 Pro"
    assert len(run.requests[0]["observations"][0]["lines"]) == 20
    run.advance(3.25)
    run.receive(message(0x18E82305, bytes.fromhex("01FFFFFFFF16F001"), stamp=3.3), 3.3)
    run.advance(5.25)
    lines = (Path(__file__).parents[1] / "fixtures/pgn-lists.candump").read_text().splitlines()
    for index, line in enumerate(lines):
        identifier, payload = line.split()[1].split("#")
        run.receive(
            message(int(identifier, 16), bytes.fromhex(payload), stamp=5.3 + index * 0.001),
            5.3 + index * 0.001,
        )
    run.advance(7.25)
    assert [r["status"] for r in run.requests] == [
        "response_observed",
        "acknowledgement_observed",
        "response_observed",
    ]
    assert run.requests[1]["observations"][0]["kind"] == "nack"
    assert {o["decoded"]["function"] for o in run.requests[2]["observations"]} == {
        "transmit",
        "receive",
    }


def test_unrelated_late_partial_and_local_candidates(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    # Wrong sender, unrelated ACK, and pre-window data cannot satisfy this request.
    run.receive(message(0x18E82306, bytes.fromhex("01FFFFFFFF14F001")), 1.3)
    run.receive(message(0x18E82305, bytes.fromhex("01FFFFFFFF16F001")), 1.4)
    assert not run.requests[0]["observations"]
    run.advance(3.25)
    # Correct NACK from a local socket is explicitly labeled; never a remote-origin assertion.
    run.receive(message(0x18E82305, bytes.fromhex("01FFFFFFFF16F001"), is_rx=False), 3.3)
    assert run.requests[1]["observations"][0]["origin_hint"] == "local_origin"
    run.advance(5.25)
    run.receive(message(0x19EEFF05, bytes.fromhex("000100"), stamp=5.3), 5.3)
    run.advance(7.25)
    assert run.requests[2]["status"] == "partial_pgn_lists"


def test_limits_stop_transmit_and_never_send_unclaimed(tmp_path, protocol):
    run = session(tmp_path)
    with pytest.raises(ValueError, match="unclaimed"):
        run.transmit(59904, b"\x14\xf0\x01", 35, 5, 0)
    acquired(run)
    for t in (1.3, 1.4, 1.5):
        run.receive(message(0x18EEFF23, b"\xff" * 8), t)
    with pytest.raises(ValueError, match="claim traffic rate"):
        run.receive(message(0x18EEFF23, b"\xff" * 8), 1.6)
    run.stopped.append("SIGINT")
    count = len(run.bus.sent)
    with pytest.raises(ValueError, match="stopped"):
        run.transmit(60928, b"\x00" * 8, 35, 255, 2)
    assert len(run.bus.sent) == count


def test_exclusive_output_and_sidecar_before_bus(monkeypatch, tmp_path, protocol):
    options = opts(tmp_path)
    monkeypatch.setattr(discovery, "preflight", lambda o: {"linkinfo": {"info_kind": "vcan"}})
    monkeypatch.setattr(discovery, "_open_bus", lambda *a: pytest.fail("CAN opened"))
    options.output.write_bytes(b"preserve evidence")
    with pytest.raises(RuntimeError):
        discovery.discover(options)
    assert options.output.read_bytes() == b"preserve evidence"
    metadata = json.loads(Path(str(options.output) + ".json").read_text())
    assert metadata["capture_created"] is False
    assert metadata["sha256"] is None


def test_duration_no_claim_and_shutdown_metadata(monkeypatch, tmp_path, protocol):
    options = opts(tmp_path, duration=1, baseline=2)
    monkeypatch.setattr(discovery, "preflight", lambda o: {"linkinfo": {"info_kind": "vcan"}})
    bus = Bus()
    bus.recv = lambda timeout: None
    monkeypatch.setattr(discovery, "_open_bus", lambda *a: bus)
    ticks = iter(i * 0.25 for i in range(30))
    monkeypatch.setattr(discovery.time, "monotonic", lambda: next(ticks))
    original = signal.getsignal(signal.SIGINT)
    summary = discovery.discover(options)
    assert summary["stop_reason"] == "duration"
    assert not bus.sent and bus.closed
    assert summary["sha256"] == hashlib.sha256(options.output.read_bytes()).hexdigest()
    assert signal.getsignal(signal.SIGINT) == original


def test_cli_requires_explicit_transmission(capsys):
    with pytest.raises(SystemExit):
        main(
            [
                "discover",
                "--channel",
                "can1",
                "--target",
                "5",
                "--unique-number",
                "123",
                "--output",
                "never-created.log",
            ]
        )


def test_full_claim_wait_after_socket_acceptance(tmp_path, protocol):
    run = session(tmp_path)
    run.clock = lambda: 1.1  # Simulate 100 ms spent in send().
    run.advance(1)
    run.advance(1.349)
    assert not run.requests
    run.advance(1.35)
    assert run.requests


def test_response_and_nack_conflict_is_inconclusive(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    lines = (Path(__file__).parents[1] / "fixtures/product.candump").read_text().splitlines()
    for index, line in enumerate(lines):
        payload = bytes.fromhex(line.split("#")[1])
        run.receive(message(0x19F01405, payload, stamp=1.3 + index * 0.001), 1.3 + index * 0.001)
    run.receive(message(0x18E82305, bytes.fromhex("01FFFFFFFF14F001")), 1.4)
    run.advance(3.25)
    assert run.requests[0]["status"] == "inconclusive_conflicting_observations"


def test_send_failure_keeps_attempt_and_shutdown(monkeypatch, tmp_path, protocol):
    options = opts(tmp_path)
    monkeypatch.setattr(discovery, "preflight", lambda o: {"linkinfo": {"info_kind": "vcan"}})
    bus = Bus()
    bus.recv = lambda timeout: None

    def fail(*args, **kwargs):
        raise OSError("TX queue failed")

    bus.send = fail
    monkeypatch.setattr(discovery, "_open_bus", lambda *a: bus)
    ticks = iter(i * 0.25 for i in range(40))
    monkeypatch.setattr(discovery.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="TX queue failed"):
        discovery.discover(options)
    result = json.loads(Path(str(options.output) + ".json").read_text())
    assert result["stop_reason"] == "error"
    assert any(e["kind"] == "send_attempt" for e in result["events"])
    assert not any(e["kind"] == "send_accepted_by_socket" for e in result["events"])
    assert result["tx_frames"] == 0
    assert bus.closed


def test_adapter_suppresses_simulator_discovery_identity(tmp_path, protocol):
    run = session(tmp_path)
    acquired(run)
    before = len(run.bus.sent)
    # An ISO product query directed at our source must not make the adapter advertise
    # simulator model/engine PGNs as the mapper's capabilities.
    run.receive(message(0x18EA2306, bytes.fromhex("14F001")), 1.3)
    assert len(run.bus.sent) == before
    assert run.manager.provenance["version"]
    assert len(run.manager.provenance["modules"]) == 3
