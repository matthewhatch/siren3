"""Opt-in real SocketCAN check; refuses physical links even if named vcan0."""

import hashlib
import json
import os
import select
import signal
import subprocess
from pathlib import Path

import pytest

from siren_mapper.evidence import read_capture
from siren_mapper.live import inspect_interface

pytestmark = pytest.mark.vcan


@pytest.fixture
def vcan_channel():
    channel = os.environ.get("SIREN_MAPPER_VCAN")
    if not channel:
        pytest.skip("set SIREN_MAPPER_VCAN=vcan0 to explicitly opt in to virtual CAN tests")
    # Once opted in, unavailable links are failures rather than hidden skips.
    metadata = inspect_interface(channel)
    if metadata["linkinfo"]["info_kind"] != "vcan":
        pytest.fail("integration tests require verified vcan; physical links are forbidden")
    return channel


def start_capture(channel, path, *options):
    import sys

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "siren_mapper",
            "capture",
            "--channel",
            channel,
            "--listen-only",
            "--output",
            str(path),
            "--duration",
            "5",
            *options,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = select.select([process.stderr], [], [], 3)[0]
    if not ready or "Listening on" not in process.stderr.readline():
        process.kill()
        stdout, stderr = process.communicate(timeout=2)
        pytest.fail(f"capture did not become ready: {stdout} {stderr}")
    return process


def test_real_vcan_receive_and_no_transmission(vcan_channel, tmp_path):
    can = pytest.importorskip("can")
    # The mapper is receive-only; this separate test peer sends one synthetic ISO
    # request only after the fixture has verified a virtual (non-physical) link.
    output = tmp_path / "vcan.log"
    with can.Bus(
        interface="socketcan", channel=vcan_channel, ignore_config=True, receive_own_messages=False
    ) as peer:
        process = start_capture(vcan_channel, output, "--count", "1")
        try:
            peer.send(
                can.Message(
                    arbitration_id=0x18EA05DB,
                    is_extended_id=True,
                    data=bytes.fromhex("14F001"),
                    check=True,
                ),
                timeout=0.5,
            )
            stdout, stderr = process.communicate(timeout=3)
            assert process.returncode == 0, stderr
            summary = json.loads(stdout)
            assert summary["frames"] == 1 and summary["stop_reason"] == "count"
            assert summary["interface_before"]["linkinfo"]["info_kind"] == "vcan"
            assert summary["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
            capture = read_capture(output)
            assert not capture["errors"]
            assert capture["records"][0]["source"] == 219
            assert capture["records"][0]["decoded"]["requested_pgn"] == 126996
            # Other sockets do not receive this peer's own messages. Any frame here
            # would be unexpected traffic (including unintended mapper TX).
            assert peer.recv(timeout=0.15) is None
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_real_vcan_signal_stop_without_traffic(vcan_channel, tmp_path, signum):
    pytest.importorskip("can")
    output = tmp_path / "stopped.log"
    process = start_capture(vcan_channel, output)
    try:
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == 0, stderr
        summary = json.loads(stdout)
        assert summary["frames"] == 0
        assert summary["stop_reason"] == signal.Signals(signum).name
        assert output.read_bytes() == b""
        assert json.loads(Path(str(output) + ".json").read_text()) == summary
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
