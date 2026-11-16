"""Passive capture gates and lifecycle; these tests never access an interface."""

import hashlib
import json
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from siren_mapper import live
from siren_mapper.cli import main
from siren_mapper.evidence import read_capture


def link(kind="vcan", **data):
    return {"ifname": "can1", "flags": ["UP"], "linkinfo": {"info_kind": kind, "info_data": data}}


def physical():
    return link(
        "can", bittiming={"bitrate": 250000}, ctrlmode=["LISTEN-ONLY"], state="ERROR-ACTIVE"
    )


def stub_ip(monkeypatch, value):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(value))

    monkeypatch.setattr(live.subprocess, "run", run)
    return calls


def message(**kwargs):
    fields = {
        "arbitration_id": 0x18EA0523,
        "is_extended_id": True,
        "is_error_frame": False,
        "is_remote_frame": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "is_rx": True,
        "dlc": 3,
        "data": bytes.fromhex("14F001"),
        "timestamp": 1760000000.123456,
    }
    return SimpleNamespace(**(fields | kwargs))


class Receiver:
    def __init__(self, items):
        self.items = iter(items)
        self.closed = False
        self.timeouts = []

    def recv(self, timeout):
        self.timeouts.append(timeout)
        item = next(self.items)
        if isinstance(item, BaseException):
            raise item
        return item

    def shutdown(self):
        self.closed = True

    def send(self, *args, **kwargs):
        pytest.fail("passive capture attempted transmission")


def receiver(monkeypatch, items):
    bus = Receiver(items)
    monkeypatch.setattr(live, "inspect_interface", lambda *args: link())
    monkeypatch.setattr(live, "_open_receiver", lambda channel: bus)
    return bus


def test_verified_vcan_and_physical(monkeypatch):
    calls = stub_ip(monkeypatch, [link()])
    assert live.inspect_interface("can1")["linkinfo"]["info_kind"] == "vcan"
    assert calls == [["ip", "-json", "-details", "link", "show", "dev", "can1"]]
    stub_ip(monkeypatch, [physical()])
    assert live.inspect_interface("can1", isolated_bench=True) == physical()
    with pytest.raises(ValueError, match="isolated-bench"):
        live.inspect_interface("can1")


@pytest.mark.parametrize(
    "change",
    [
        {"bittiming": {"bitrate": 500000}},
        {"bittiming": {}},
        {"bittiming": None},
        {"ctrlmode": []},
        {"ctrlmode": ["LISTEN-ONLY", "LOOPBACK"]},
        {"ctrlmode": ["LISTEN-ONLY", "FD"]},
        {"ctrlmode": "LISTEN-ONLY"},
        {"ctrlmode": ["LISTEN-ONLY", "PRESUME-ACK"]},
        {"state": "BUS-OFF"},
        {"state": None},
    ],
)
def test_physical_fail_closed(monkeypatch, change):
    value = physical()
    value["linkinfo"]["info_data"].update(change)
    stub_ip(monkeypatch, [value])
    with pytest.raises(ValueError):
        live.inspect_interface("can1", isolated_bench=True)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        [None],
        [link(), link()],
        [{"ifname": "can1", "flags": ["UP"]}],
        [{"ifname": "can1", "flags": ["UP"], "linkinfo": None}],
        [link("ether")],
        [dict(link(), flags=[])],
        [dict(link(), ifname="other")],
    ],
)
def test_bad_interface_metadata(monkeypatch, value):
    stub_ip(monkeypatch, value)
    with pytest.raises(ValueError):
        live.inspect_interface("can1", True)


@pytest.mark.parametrize("channel", ["", "any", "-a", "can1;echo", "x" * 16])
def test_explicit_valid_channel(monkeypatch, channel):
    # 'any' is a valid Linux name but cannot be a wildcard: exact name metadata is required.
    calls = stub_ip(monkeypatch, [link()])
    with pytest.raises(ValueError):
        live.inspect_interface(channel)
    if channel != "any":
        assert not calls


def test_ip_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ip", 5)

    monkeypatch.setattr(live.subprocess, "run", fail)
    with pytest.raises(ValueError, match="cannot verify"):
        live.inspect_interface("can1")


def test_count_capture_replay_hash_and_shutdown(monkeypatch, tmp_path):
    bus = receiver(monkeypatch, [None, message(), message(is_rx=False)])
    output = tmp_path / "capture.log"
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    result = live.capture("can1", output, listen_only=True, count=2)
    assert result["stop_reason"] == "count"
    assert result["frames"] == 2
    assert bus.closed and all(0 < t <= 0.1 for t in bus.timeouts)
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(Path(str(output) + ".json").read_text()) == result
    assert all(signal.getsignal(s) == handler for s, handler in before.items())
    parsed = read_capture(output)
    assert not parsed["errors"]
    assert [r["frames"][0]["direction"] for r in parsed["records"]] == ["R", "T"]
    assert parsed["records"][0]["decoded"]["requested_pgn"] == 126996


def test_idle_duration(monkeypatch, tmp_path):
    bus = receiver(monkeypatch, [None] * 10)
    ticks = iter([0, 0, 0, 0.1, 0.2, 0.3, 0.4])
    monkeypatch.setattr(live.time, "monotonic", lambda: next(ticks))
    result = live.capture("can1", tmp_path / "quiet.log", listen_only=True, duration=0.2)
    assert result["frames"] == 0 and result["stop_reason"] == "duration"
    assert bus.closed


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_stop_flushes_and_restores(monkeypatch, tmp_path, signum):
    bus = receiver(monkeypatch, [])
    previous = signal.getsignal(signum)

    def recv(timeout):
        signal.getsignal(signum)(signum, None)
        return message()

    bus.recv = recv
    result = live.capture("can1", tmp_path / "stop.log", listen_only=True)
    assert result["stop_reason"] == signal.Signals(signum).name
    assert result["frames"] == 1
    assert bus.closed and signal.getsignal(signum) == previous


@pytest.mark.parametrize("failure", [RuntimeError("receive failed"), KeyboardInterrupt()])
def test_error_or_interrupt_preserves_capture(monkeypatch, tmp_path, failure):
    bus = receiver(monkeypatch, [message(), failure])
    output = tmp_path / "partial.log"
    if isinstance(failure, KeyboardInterrupt):
        assert live.capture("can1", output, listen_only=True)["stop_reason"] == "SIGINT"
    else:
        with pytest.raises(RuntimeError, match="receive failed"):
            live.capture("can1", output, listen_only=True)
    assert bus.closed
    assert len(output.read_text().splitlines()) == 1
    metadata = json.loads(Path(str(output) + ".json").read_text())
    assert metadata["frames"] == 1
    assert metadata["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.parametrize("collision", ["raw", "metadata"])
def test_existing_evidence_never_overwritten(monkeypatch, tmp_path, collision):
    monkeypatch.setattr(live, "inspect_interface", lambda *args: link())
    monkeypatch.setattr(live, "_open_receiver", lambda *args: pytest.fail("socket opened"))
    output = tmp_path / "keep.log"
    existing = output if collision == "raw" else Path(str(output) + ".json")
    existing.write_bytes(b"original evidence")
    with pytest.raises((OSError, RuntimeError)):
        live.capture("can1", output, listen_only=True)
    assert existing.read_bytes() == b"original evidence"


@pytest.mark.parametrize(
    "options",
    [
        {"listen_only": False},
        {"listen_only": True, "duration": 0},
        {"listen_only": True, "duration": float("nan")},
        {"listen_only": True, "duration": float("inf")},
        {"listen_only": True, "count": 0},
    ],
)
def test_invalid_options_before_interface_or_file(monkeypatch, tmp_path, options):
    monkeypatch.setattr(live, "inspect_interface", lambda *args: pytest.fail("interface checked"))
    with pytest.raises(ValueError):
        live.capture("can1", tmp_path / "bad.log", **options)
    assert not list(tmp_path.iterdir())


def test_socketcan_config_cannot_override_passive(monkeypatch):
    can = pytest.importorskip("can")
    calls = []
    monkeypatch.setattr(can, "Bus", lambda **kwargs: calls.append(kwargs))
    live._open_receiver("can1")
    assert calls == [
        {
            "interface": "socketcan",
            "channel": "can1",
            "ignore_config": True,
            "receive_own_messages": False,
            "local_loopback": False,
            "fd": False,
        }
    ]


def test_unsupported_frames_retained(monkeypatch, tmp_path):
    receiver(
        monkeypatch,
        [
            message(is_extended_id=False, arbitration_id=0x123),
            message(is_remote_frame=True),
            message(is_fd=True),
            message(is_error_frame=True),
        ],
    )
    output = tmp_path / "unsupported.log"
    result = live.capture("can1", output, listen_only=True, count=4)
    assert result["unsupported_frames"] == 4
    assert "123#14F001" in output.read_text()
    assert "18EA0523#R3" in output.read_text()
    assert "18EA0523##014F001" in output.read_text()
    assert "38EA0523#14F001" in output.read_text()
    assert len(read_capture(output)["errors"]) == 4


def test_cli_requires_explicit_mode_and_channel(capsys):
    for args in (
        ["capture"],
        ["capture", "--channel", "can1", "--output", "a"],
        ["capture", "--listen-only", "--output", "a"],
    ):
        with pytest.raises(SystemExit):
            main(args)


def test_cli_capture_summary_and_failure(monkeypatch, tmp_path, capsys):
    receiver(monkeypatch, [message()])
    assert (
        main(
            [
                "capture",
                "--channel",
                "can1",
                "--listen-only",
                "--output",
                str(tmp_path / "cli.log"),
                "--count",
                "1",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "count"
    assert (
        main(
            [
                "capture",
                "--channel",
                "can1",
                "--listen-only",
                "--output",
                str(tmp_path / "cli.log"),
                "--count",
                "1",
            ]
        )
        == 2
    )


def test_open_failure_keeps_diagnostic_and_restores_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "inspect_interface", lambda *args: link())
    previous = signal.getsignal(signal.SIGINT)

    def fail(channel):
        raise OSError("socket unavailable")

    monkeypatch.setattr(live, "_open_receiver", fail)
    output = tmp_path / "startup.log"
    with pytest.raises(RuntimeError, match="socket unavailable"):
        live.capture("can1", output, listen_only=True)
    metadata = json.loads(Path(str(output) + ".json").read_text())
    assert metadata["stop_reason"] == "error"
    assert metadata["capture_created"] is True
    assert metadata["sha256"] == hashlib.sha256(b"").hexdigest()
    assert signal.getsignal(signal.SIGINT) == previous


def test_shutdown_failure_is_logged(monkeypatch, tmp_path):
    bus = receiver(monkeypatch, [message()])

    def fail():
        raise OSError("shutdown failed")

    bus.shutdown = fail
    output = tmp_path / "shutdown.log"
    with pytest.raises(RuntimeError, match="shutdown failed"):
        live.capture("can1", output, listen_only=True, count=1)
    metadata = json.loads(Path(str(output) + ".json").read_text())
    assert metadata["stop_reason"] == "error"
    assert metadata["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_failed_physical_preflight_never_opens_socket_or_output(monkeypatch, tmp_path):
    value = physical()
    value["linkinfo"]["info_data"]["ctrlmode"] = []
    stub_ip(monkeypatch, [value])
    monkeypatch.setattr(live, "_open_receiver", lambda *args: pytest.fail("socket opened"))
    with pytest.raises(ValueError, match="LISTEN-ONLY"):
        live.capture("can1", tmp_path / "forbidden.log", listen_only=True, isolated_bench=True)
    assert not list(tmp_path.iterdir())
