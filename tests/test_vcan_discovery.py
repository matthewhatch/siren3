"""Explicit opt-in discovery against a virtual peer; never a physical device."""

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest

from siren_mapper.evidence import read_capture
from siren_mapper.live import inspect_interface
from siren_mapper.reports import compare

pytestmark = pytest.mark.vcan


@pytest.fixture
def virtual_channel():
    channel = os.environ.get("SIREN_MAPPER_VCAN")
    if not channel:
        pytest.skip("set SIREN_MAPPER_VCAN=vcan0 to opt in to virtual-only discovery")
    link = inspect_interface(channel)
    if link["linkinfo"]["info_kind"] != "vcan":
        pytest.fail("active tests refuse physical interfaces, regardless of their names")
    pytest.importorskip("can")
    pytest.importorskip("nmea_sim.device")
    return channel


def command(channel, output, *extra):
    return [
        sys.executable,
        "-m",
        "siren_mapper",
        "discover",
        "--channel",
        channel,
        "--target",
        "5",
        "--unique-number",
        "123456",
        "--allow-transmit",
        "--output",
        str(output),
        "--baseline",
        "0.2",
        "--response-window",
        "0.3",
        "--recovery",
        "0.2",
        "--duration",
        "8",
        *extra,
    ]


@pytest.mark.parametrize("collide", [False, True])
def test_virtual_discovery_candidates_and_baseline_diff(virtual_channel, tmp_path, collide):
    import can
    from nmea_sim import device_protocol as p
    from nmea_sim.can_id import compose, extract
    from nmea_sim.device import TX_PGNS, Device, Transmission

    peer = Device(p.Name(unique_number=42, manufacturer_code=2046), preferred_address=5)
    peer.product = p.ProductInformation(
        model_id="VIRTUAL DISCOVERY PEER", serial_code="synthetic-42"
    )
    halt = threading.Event()
    ready = threading.Event()
    errors = []
    seen_requests = []
    claim_times = {}

    def serve():
        try:
            with can.Bus(
                interface="socketcan",
                channel=virtual_channel,
                ignore_config=True,
                receive_own_messages=False,
            ) as bus:

                def send(items):
                    for item in items:
                        bus.send(
                            can.Message(
                                arbitration_id=compose(
                                    item.pgn,
                                    source=item.source,
                                    destination=item.destination,
                                    priority=item.priority,
                                ),
                                data=item.data,
                                is_extended_id=True,
                                check=True,
                            ),
                            timeout=0.1,
                        )

                send(peer.start(time.monotonic()))
                ready.set()
                while not halt.is_set():
                    now = time.monotonic()
                    msg = bus.recv(timeout=0.01)
                    if msg is not None:
                        ident = extract(msg.arbitration_id)
                        if ident.pgn == p.CLAIM:
                            claim_times[ident.source] = now
                            if collide and ident.source == 35:
                                send([Transmission(p.CLAIM, b"\x00" * 8, 35)])
                        if ident.pgn == p.REQUEST and ident.destination == 5:
                            assert ident.source in claim_times
                            assert now - claim_times[ident.source] >= 0.24
                            requested = p.decode_request(bytes(msg.data))
                            seen_requests.append((requested, ident.source, now))
                            if requested == p.CONFIGURATION:
                                send(
                                    [
                                        Transmission(
                                            p.ACK,
                                            p.Acknowledgement(1, 255, requested).encode(),
                                            5,
                                            ident.source,
                                        )
                                    ]
                                )
                                continue
                        send(peer.receive(msg, now))
                    send(peer.tick(now))
        except (AssertionError, OSError, ValueError, can.CanError) as exc:
            errors.append(exc)
            ready.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)
    output = tmp_path / "discovery.candump"
    try:
        run = subprocess.run(
            command(virtual_channel, output),
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        assert run.returncode == 0, run.stderr
        result = json.loads(run.stdout)
        assert result["stop_reason"] == "complete"
        assert result["tx_frames"] <= 6
        assert [request["status"] for request in result["requests"]] == [
            "response_observed",
            "response_observed",
            "acknowledgement_observed",
            "response_observed",
        ]
        assert (
            result["requests"][1]["observations"][0]["decoded"]["model"] == "VIRTUAL DISCOVERY PEER"
        )
        assert result["requests"][2]["observations"][0]["kind"] == "nack"
        assert [request[0] for request in seen_requests] == [60928, 126996, 126998, 126464]
        assert all(b[2] - a[2] >= 0.99 for a, b in pairwise(seen_requests))
        if collide:
            assert result["final_source"] != 35
            assert any(event["kind"] == "source_conflict" for event in result["events"])
        from siren_mapper.profiles import load_profile, profile_from_capture

        fingerprint = load_profile(Path(result["profile"]))
        assert profile_from_capture(output, target=5) == fingerprint
        assert fingerprint["session"]["stop_reason"] == "complete"
        target = next(d for d in fingerprint["devices"] if d["identity"]["name_hex"])
        assert target["identity"]["name_hex"] == f"{peer.name.value:016x}"
        assert target["product"]["fields"]["model"] == "VIRTUAL DISCOVERY PEER"
        assert target["configuration"]["status"] == "request_not_accepted"
        assert target["advertised_pgns"]["transmit"]["pgns"] == list(TX_PGNS)
        assert target["advertised_pgns"]["receive"]["pgns"]
        assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert json.loads(Path(str(output) + ".json").read_text()) == result
        decoded = read_capture(output)
        assert not decoded["errors"]
        assert any(
            r.get("decoded", {}).get("model") == "VIRTUAL DISCOVERY PEER"
            for r in decoded["records"]
        )
        # Preserve and replay the session's pre-transmit baseline as a derived artifact.
        # Its parent capture hash and exact end line are recorded by the session.
        end = next(
            event["final_line"]
            for event in result["events"]
            if event["kind"] == "baseline_complete"
        )
        baseline = tmp_path / "baseline.candump"
        baseline.write_bytes(b"".join(output.read_bytes().splitlines(keepends=True)[:end]))
        diff = compare(read_capture(baseline), decoded)
        assert diff["result"] == "observed_change"
        assert any(change["pgn"] == 126996 for change in diff["changes"])
    finally:
        halt.set()
        thread.join(timeout=2)
    assert not errors
    assert not thread.is_alive()


def test_virtual_discovery_signal_cancels_before_claim(virtual_channel, tmp_path):
    import can

    output = tmp_path / "cancelled.candump"
    with can.Bus(
        interface="socketcan",
        channel=virtual_channel,
        ignore_config=True,
        receive_own_messages=False,
    ) as monitor:
        process = subprocess.Popen(
            command(virtual_channel, output, "--baseline", "3"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert select.select([process.stderr], [], [], 3)[0]
            assert "Discovery baseline" in process.stderr.readline()
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=2)
            assert process.returncode == 0, stderr
            summary = json.loads(stdout)
            assert summary["stop_reason"] == "SIGTERM"
            assert summary["tx_frames"] == 0
            assert monitor.recv(timeout=0.1) is None
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
