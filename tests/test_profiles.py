"""End-to-end device fingerprints with observed bytes and labeled synthetic cases."""

import hashlib
import json
from pathlib import Path

import pytest

from siren_mapper.cli import main
from siren_mapper.profiles import (
    diff_profiles,
    load_profile,
    profile_from_capture,
    save_profile,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NAME = "c08cab00815fffff"
TX = [59392, 59904, 60160, 60416, 60928, 126208, 126464, 126993, 126996]
RX = [59392, 59904, 60160, 60416, 60928, 65240, 126208, 127488, 127489, 127505]


def siren():
    return profile_from_capture(FIXTURES / "siren-fingerprint.candump", target=5)


def named(profile):
    return next(d for d in profile["devices"] if d["identity"]["name_hex"] == NAME)


def test_complete_recorded_siren_fingerprint():
    profile = siren()
    device = named(profile)
    assert len(profile["devices"]) == 1
    assert device["identity"]["fields"] == {
        "name_hex": NAME,
        "unique_number": 2097151,
        "manufacturer_code": 1034,
        "device_instance_lower": 0,
        "device_instance_upper": 0,
        "device_function": 171,
        "reserved": 0,
        "device_class": 70,
        "system_instance": 0,
        "industry_group": 4,
        "arbitrary_address_capable": 1,
    }
    assert device["product"]["fields"] == {
        "nmea_version": 3100,
        "product_code": 13939,
        "model": "Siren 3 Pro",
        "software": "1.0.0",
        "model_version": None,
        "serial": "21003942",
        "certification_level": 2,
        "load_equivalency": 1,
    }
    assert len(device["product"]["observations"][0]["record"]["frames"]) == 20
    assert device["configuration"]["status"] == "request_not_accepted"
    assert device["configuration"]["fields"] is None
    nack = device["configuration"]["observations"][0]
    assert nack["record"]["decoded"]["control"] == 1
    assert nack["record"]["payload_hex"] == "01ffffffff16f001"
    assert nack["request"]["payload_hex"] == "16f001"
    assert nack["target_name"] == NAME
    assert nack["request_target_name"] == NAME
    assert nack["correlation"] == "capture_order_only_timing_unknown"
    assert device["advertised_pgns"]["transmit"]["pgns"] == TX
    assert device["advertised_pgns"]["receive"]["pgns"] == RX
    assert device["advertised_pgns"]["transmit"]["observations"][0]["record"]["priority"] == 6
    assert device["advertised_pgns"]["receive"]["observations"][0]["record"]["priority"] == 0
    assert not profile["errors"]


def test_missing_config_distinct_from_nack():
    profile = profile_from_capture(FIXTURES / "fingerprinting/missing-configuration.candump")
    assert named(profile)["configuration"]["status"] == "timeout"
    assert named(profile)["configuration"]["observations"] == []
    assert siren()["devices"][0]["configuration"]["status"] != "timeout"


def test_unknown_and_malformed_cases():
    unknown = profile_from_capture(FIXTURES / "fingerprinting/unknown-device.candump")
    assert unknown["devices"][0]["identity"]["confidence"] == "unknown"
    assert unknown["devices"][0]["product"]["fields"] is None
    bad = profile_from_capture(FIXTURES / "fingerprinting/malformed-fast-packet.candump")
    assert bad["errors"]
    assert named(bad)["product"]["fields"] is None
    assert named(bad)["product"]["status"] == "no_response_observed"


def test_source_collision_does_not_merge_names():
    profile = profile_from_capture(FIXTURES / "fingerprinting/source-collision.candump")
    assert len(profile["devices"]) == 2
    assert profile["identity_transitions"][0]["kind"] == "address_identity_changed"
    assert all(d["locations"] == [{"channel": "can1", "source": 5}] for d in profile["devices"])
    assert len(named(profile)["evidence"]) == 1  # later traffic belongs to the other claim


def test_duplicate_and_wrong_source_replies():
    profile = profile_from_capture(
        FIXTURES / "fingerprinting/duplicate-unexpected.candump", target=5
    )
    device = named(profile)
    observations = device["product"]["observations"]
    assert len(observations) == 2
    assert observations[0]["duplicate_of"] is None
    assert observations[1]["duplicate_of"] == observations[0]["record"]["evidence_id"]
    assert device["product"]["status"] == "response_observed"
    assert any(
        o["reason"] == "no matching current captured request"
        for o in profile["unexpected_observations"]
    )
    assert len(profile["devices"]) == 1  # wrong-source node never becomes the selected Siren


def test_source_change_separate_from_identity_and_contents(tmp_path):
    original = (FIXTURES / "siren-fingerprint.candump").read_text()
    moved = original
    for before, after in [
        ("18EEFF05", "18EEFF06"),
        ("18EA0523", "18EA0623"),
        ("19F01405", "19F01406"),
        ("18E82305", "18E82306"),
        ("19EEFF05", "19EEFF06"),
        ("01EEFF05", "01EEFF06"),
    ]:
        moved = moved.replace(before, after)
    path = tmp_path / "moved.log"
    path.write_text(moved)
    result = diff_profiles(siren(), profile_from_capture(path, target=6))
    assert not result["unmatched_before"] and not result["unmatched_after"]
    match = result["matched_by_observed_name"][0]
    assert match["name_hex"] == NAME
    assert match["address_changed"]
    assert match["content_changes"] == {}


def test_unknown_addresses_never_matched_across_runs():
    profile = profile_from_capture(FIXTURES / "fingerprinting/unknown-device.candump")
    result = diff_profiles(profile, profile)
    assert not result["matched_by_observed_name"]
    assert result["unmatched_before"] and result["unmatched_after"]


def test_profiles_saved_show_diff_and_preserve_raw(tmp_path, capsys):
    raw = FIXTURES / "siren-fingerprint.candump"
    original = raw.read_bytes()
    profile = siren()
    path = tmp_path / "profile.json"
    save_profile(profile, path)
    assert load_profile(path) == profile
    assert "request not accepted" in Path(str(path) + ".md").read_text()
    assert main(["profile", "show", str(path)]) == 0
    assert "Siren 3 Pro" in capsys.readouterr().out
    assert main(["profile", "diff", str(path), str(path)]) == 0
    assert not json.loads(capsys.readouterr().out)["matched_by_observed_name"][0]["address_changed"]
    with pytest.raises(FileExistsError):
        save_profile(profile, path)
    assert raw.read_bytes() == original
    assert profile["capture"]["sha256"] == hashlib.sha256(original).hexdigest()


def test_offline_discover_no_socket_and_repeat_snapshots(monkeypatch, tmp_path, capsys):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("offline socket opened"))
    raw = tmp_path / "recorded.log"
    raw.write_bytes((FIXTURES / "siren-fingerprint.candump").read_bytes())
    for index in (1, 2):
        assert main(["discover", "--capture", str(raw), "--target", "5"]) == 0
        output = capsys.readouterr()
        assert named(json.loads(output.out))["product"]["fields"]["serial"] == "21003942"
    first = load_profile(Path(str(raw) + ".profile.json"))
    second = load_profile(Path(str(raw) + ".profile-002.json"))
    assert first == second


def test_passive_devices_capture_and_live_never_send(monkeypatch, tmp_path, capsys):
    from siren_mapper import live

    class Peer:
        def recv(self, timeout):
            from types import SimpleNamespace

            return SimpleNamespace(
                arbitration_id=0x18EEFF05,
                is_extended_id=True,
                is_error_frame=False,
                is_remote_frame=False,
                is_fd=False,
                is_rx=True,
                timestamp=1.0,
                data=bytes.fromhex("FFFF5F8100AB8CC0"),
            )

        def send(self, *a, **kw):
            pytest.fail("passive devices attempted CAN TX")

        def shutdown(self):
            pass

    monkeypatch.setattr(live, "inspect_interface", lambda *a: {"linkinfo": {"info_kind": "vcan"}})
    monkeypatch.setattr(live, "_open_receiver", lambda *a: Peer())
    raw = tmp_path / "devices.log"
    assert main(["devices", "--interface", "vcan0", "--output", str(raw), "--count", "1"]) == 0
    assert NAME in capsys.readouterr().out
    assert Path(str(raw) + ".profile.json").exists()
    assert main(["devices", "--capture", str(raw), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["devices"][0]["identity"]["name_hex"] == NAME


def test_offline_modes_reject_transmission_flags_before_socket(tmp_path):
    with pytest.raises(SystemExit):
        main(["discover", "--capture", "missing", "--allow-tx"])
    with pytest.raises(SystemExit):
        main(["discover", "--capture", "missing", "--interface", "can1"])


def test_capture_end_without_timing_is_not_claimed_timeout(tmp_path):
    path = tmp_path / "short.log"
    path.write_text("can1 18EEFF05#FFFF5F8100AB8CC0\ncan1 18EA0523#16F001\n")
    profile = profile_from_capture(path)
    assert named(profile)["configuration"]["status"] == "no_response_observed"


def test_wrong_destination_nack_is_not_our_request_not_accepted(tmp_path):
    path = tmp_path / "wrong.log"
    path.write_text(
        "can1 18EEFF05#FFFF5F8100AB8CC0\ncan1 18EA0523#16F001\ncan1 18E82205#01FFFFFFFF16F001\n"
    )
    profile = profile_from_capture(path)
    assert named(profile)["configuration"]["status"] == "uncorrelated_acknowledgement_observed"
    assert any("destination" in o["reason"] for o in profile["unexpected_observations"])


def test_product_without_optional_cert_load_is_supported():
    from siren_mapper.protocol import decode_payload

    complete = named(siren())["product"]["observations"][0]["record"]["payload_hex"]
    decoded = decode_payload(126996, bytes.fromhex(complete)[:132])
    assert decoded["model"] == "Siren 3 Pro"
    assert decoded["certification_level"] is None and decoded["load_equivalency"] is None


def test_sidecar_windows_used_only_with_matching_capture_hash(tmp_path):
    raw = tmp_path / "session.log"
    raw.write_text("can1 18EEFF05#FFFF5F8100AB8CC0\ncan1 18EA0523#16F001\n")
    metadata = {
        "mode": "active_discovery",
        "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "requests": [{"request_line": 2, "pgn": 126998, "status": "timeout"}],
    }
    Path(str(raw) + ".json").write_text(json.dumps(metadata))
    profile = profile_from_capture(raw)
    assert named(profile)["configuration"]["status"] == "timeout"
    metadata["sha256"] = "wrong hash"
    Path(str(raw) + ".json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="hash does not match"):
        profile_from_capture(raw)


def test_assembly_across_name_replacement_is_never_bound_to_either_identity(tmp_path):
    product = (FIXTURES / "product.candump").read_text().splitlines()
    raw = tmp_path / "interleaved.log"
    raw.write_text(
        "can1 18EEFF05#FFFF5F8100AB8CC0\n" + product[0] + "\n"
        "can1 18EEFF05#E69FD8FF008C64C0\n" + "\n".join(product[1:]) + "\n"
    )
    profile = profile_from_capture(raw)
    owner = next(d for d in profile["devices"] if d["product"]["fields"])
    assert owner["identity"]["name_hex"] is None
    assert owner["device_id"].startswith("uncertain:")


def test_read_only_discover_without_allow_tx_uses_receiver(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from siren_mapper import discovery, live

    bus = SimpleNamespace(
        recv=lambda timeout: SimpleNamespace(
            arbitration_id=0x18EEFF05,
            is_extended_id=True,
            is_error_frame=False,
            is_remote_frame=False,
            is_fd=False,
            is_rx=True,
            timestamp=1,
            data=bytes.fromhex("FFFF5F8100AB8CC0"),
        ),
        shutdown=lambda: None,
    )
    monkeypatch.setattr(live, "inspect_interface", lambda *a: {"linkinfo": {"info_kind": "vcan"}})
    monkeypatch.setattr(live, "_open_receiver", lambda *a: bus)
    monkeypatch.setattr(discovery, "_open_bus", lambda *a: pytest.fail("active socket opened"))
    assert (
        main(
            [
                "discover",
                "--interface",
                "vcan0",
                "--target",
                "5",
                "--count",
                "1",
                "--output",
                str(tmp_path / "passive.log"),
            ]
        )
        == 0
    )
    assert named(json.loads(capsys.readouterr().out))["identity"]["name_hex"] == NAME


def test_interrupted_window_does_not_look_like_completed_request(tmp_path):
    path = tmp_path / "partial.log"
    path.write_bytes((FIXTURES / "siren-fingerprint.candump").read_bytes())
    profile = profile_from_capture(path)
    product_request = next(r for r in profile["requests"] if r["pgn"] == 126996)
    metadata = {
        "requests": [
            {
                "request_line": product_request["request"]["provenance"]["lines"][0],
                "pgn": 126996,
                "status": "inconclusive_address_change",
            }
        ]
    }
    interrupted = profile_from_capture(path, session=metadata)
    assert named(interrupted)["product"]["status"] == "inconclusive_request_window"
    assert named(interrupted)["product"]["fields"]["model"] == "Siren 3 Pro"  # retained observation


def test_conflicting_ack_controls_not_last_reply_wins(tmp_path):
    path = tmp_path / "conflict.log"
    path.write_text(
        "can1 18EEFF05#FFFF5F8100AB8CC0\ncan1 18EA0523#16F001\n"
        "can1 18E82305#01FFFFFFFF16F001\ncan1 18E82305#03FFFFFFFF16F001\n"
    )
    profile = profile_from_capture(path)
    assert profile["requests"][0]["status"] == "conflicting_observations"
    assert named(profile)["configuration"]["status"] == "conflicting_observations"
    before = siren()
    assert diff_profiles(before, profile)["matched_by_observed_name"][0]["content_changes"]


def test_recorded_monotonic_window_qualification_reused_offline(tmp_path):
    raw = tmp_path / "live.log"
    raw.write_bytes((FIXTURES / "siren-fingerprint.candump").read_bytes())
    initial = profile_from_capture(raw)
    product = named(initial)["product"]["observations"][0]
    request_line = product["request"]["provenance"]["lines"][0]
    window = {
        "requests": [
            {
                "request_line": request_line,
                "pgn": 126996,
                "status": "response_observed",
                "observations": [{"lines": product["record"]["provenance"]["lines"]}],
            }
        ]
    }
    rebuilt = profile_from_capture(raw, session=window)
    assert (
        named(rebuilt)["product"]["observations"][0]["correlation"]
        == "recorded_monotonic_request_window"
    )
    window["requests"][0].update(status="timeout", observations=[])
    excluded = profile_from_capture(raw, session=window)
    assert named(excluded)["product"]["observations"][0]["request"] is None
    assert named(excluded)["product"]["requests"][0]["status"] == "timeout"
