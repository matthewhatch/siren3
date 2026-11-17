"""Evidence acceptance tests use real snippets and explicitly synthetic variations."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from siren_mapper.cli import main
from siren_mapper.evidence import read_capture
from siren_mapper.reports import compare, inventory, markdown, report

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def write_capture(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return read_capture(path)


def test_evidence_fields_and_provenance():
    path = FIXTURES / "product.candump"
    capture = read_capture(path)
    record = capture["records"][0]
    assert record["evidence_id"] == "message-1"
    assert record["classification"] == "decoded_fact"
    assert record["confidence"]["level"] == "supported"
    assert record["timestamp"] is None
    assert record["can_id"] == "19F01405"
    assert record["provenance"] == {
        "file": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "lines": list(range(1, 21)),
    }
    assert len(capture["raw_frames"]) == 20
    assert any("timing" in q for q in record["unresolved_questions"])


def test_timestamps_and_direction_preserved(tmp_path):
    capture = write_capture(tmp_path, "stamp.log", "(1760000000.123400) can1 R 18EA0523#14F001\n")
    record = capture["records"][0]
    assert record["timestamp"] == "1760000000.123400"
    assert record["frames"][0]["direction"] == "R"
    assert not any("timing" in q for q in record["unresolved_questions"])
    assert any("echo" in q for q in record["unresolved_questions"])


def test_inventory_separates_observed_and_advertised():
    result = inventory(read_capture(FIXTURES / "pgn-lists.candump"))
    source = result["sources"][0]
    assert (source["channel"], source["source"]) == ("can1", 5)
    assert source["observed_pgns"] == [126464]
    assert source["frame_count"] == 10
    assert source["message_count"] == 2
    assert [o["decoded"]["function"] for o in source["observations"]] == ["transmit", "receive"]
    assert 127488 in source["observations"][1]["decoded"]["pgns"]
    assert source["observations"][0]["provenance"]["lines"] == [1, 2, 3, 4, 5]


def test_inventory_preserves_conflicting_identity_observations(tmp_path):
    capture = write_capture(
        tmp_path,
        "claims.log",
        "can1 18EEFF05#FFFF5F8100AB8CC0\n"
        "can1 18EEFF05#E69FD8FF008C64C0\n"
        "can2 18EEFF05#E69FD8FF008C64C0\n",
    )
    sources = inventory(capture)["sources"]
    assert len(sources) == 2
    observations = sources[0]["observations"]
    assert len(observations) == 2
    assert observations[0]["decoded"]["name_hex"] != observations[1]["decoded"]["name_hex"]
    assert observations[0]["evidence_id"] != observations[1]["evidence_id"]


def test_malformed_and_incomplete_raw_evidence(tmp_path):
    text = (FIXTURES / "product.candump").read_text().splitlines()[0]
    capture = write_capture(tmp_path, "broken.log", text + "\ninvalid line\n")
    assert len(capture["raw_frames"]) == 1
    assert capture["records"] == []
    assert len(capture["errors"]) == 2
    assert capture["errors"][0]["raw"] == "invalid line"
    assert capture["errors"][0]["provenance"]["line"] == 2
    assert capture["errors"][1]["line"] is None
    assert report(capture)["summary"]["raw_frame_count"] == 1
    assert inventory(capture)["sources"][0]["observed_pgns"] == [126996]
    assert compare(capture, capture)["result"] == "inconclusive"


def test_unknown_and_anomaly_are_unresolved(tmp_path):
    unknown = write_capture(tmp_path, "unknown.log", "can1 09F30023#000000FFFFFFFFFF\n")
    record = unknown["records"][0]
    assert record["status"] == "unknown"
    assert record["classification"] == "unknown"
    assert record["confidence"]["level"] == "unresolved"
    assert record["decoded"]["unknown_payload_hex"] == "000000ffffffffff"
    malformed = read_capture(FIXTURES / "discovery.candump")["records"][-1]
    assert malformed["status"] == "malformed"
    assert malformed["confidence"]["level"] == "unresolved"
    assert "decoded" not in malformed
    assert any("anomaly" in q for q in malformed["unresolved_questions"])


def test_diff_added_removed_changed_and_evidence(tmp_path):
    before = write_capture(
        tmp_path, "before.log", "can1 18EA0523#14F001\ncan1 18EEFF05#FFFF5F8100AB8CC0\n"
    )
    after = write_capture(
        tmp_path, "after.log", "can1 18EA0523#16F001\ncan1 09F20023#000000FFFFFFFFFF\n"
    )
    result = compare(before, after)
    assert result["result"] == "observed_change"
    assert {(c["pgn"], c["change"]) for c in result["changes"]} == {
        (60928, "removed"),
        (59904, "changed"),
        (127488, "added"),
    }
    changed = next(c for c in result["changes"] if c["pgn"] == 59904)
    assert changed["baseline"]["payloads"] == ["14f001"]
    assert changed["stimulus"]["payloads"] == ["16f001"]
    assert changed["baseline_evidence"] == ["message-1"]
    assert result["evidence"]["baseline"][0]["provenance"]["sha256"] == before["capture"]["sha256"]
    assert any("stimulus" in q for q in result["unresolved_questions"])


def test_diff_counts_payload_frequency_and_ignores_metadata(tmp_path):
    a, b = "can1 18EA0523#14F001\n", "can1 18EA0523#16F001\n"
    before = write_capture(tmp_path, "before.log", a + a + b)
    after = write_capture(tmp_path, "after.log", a + b + b)
    result = compare(before, after)
    assert result["result"] == "observed_change"
    assert result["changes"][0]["baseline"]["payload_counts"] == {"14f001": 2, "16f001": 1}
    same = write_capture(
        tmp_path,
        "same.log",
        "(123.001) can1 18EA0523#14F001 T\n"
        "(123.002) can1 18EA0523#14F001 R\n"
        "(123.003) can1 18EA0523#16F001 R\n",
    )
    assert compare(before, same)["result"] == "no_observed_change"


def test_empty_capture(tmp_path):
    empty = write_capture(tmp_path, "empty.log", "# no observations\n")
    assert inventory(empty)["sources"] == []
    assert compare(empty, empty)["result"] == "no_observed_change"
    assert report(empty)["summary"]["message_count"] == 0
    assert "Frames: 0" in markdown(report(empty))


@pytest.mark.parametrize(
    "command", ["decode", "inventory", "diff", "report-json", "report-markdown"]
)
def test_commands_deterministic_read_only_offline(command, monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("offline reporting attempted socket access")

    monkeypatch.setattr(socket, "socket", forbidden)
    path = tmp_path / "capture.log"
    raw = (FIXTURES / "product.candump").read_bytes()
    path.write_bytes(raw)
    if command.startswith("report-"):
        args = ["report", str(path), "--format", command.split("-")[1]]
    elif command == "diff":
        args = ["diff", str(path), str(path)]
    else:
        args = [command, str(path)]
    assert main(args) == 0
    first = capsys.readouterr().out
    assert main(args) == 0
    assert capsys.readouterr().out == first
    assert path.read_bytes() == raw
    assert sorted(p.name for p in tmp_path.iterdir()) == ["capture.log"]
    if command == "report-markdown":
        assert "# Siren Mapper evidence report" in first
        assert "Siren 3 Pro" in first
        assert hashlib.sha256(raw).hexdigest() in first
    else:
        assert json.loads(first)["schema_version"] == 1


@pytest.mark.parametrize("command", ["inventory", "report", "diff"])
def test_commands_error_status(command, capsys):
    path = str(FIXTURES / "discovery.candump")
    args = [command, path, path] if command == "diff" else [command, path]
    assert main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["errors"]
    assert (
        main([command, "/missing", "/missing"] if command == "diff" else [command, "/missing"]) == 2
    )


def test_markdown_escapes_untrusted_capture_content(tmp_path):
    capture = write_capture(tmp_path, "markup.log", "can|<script> 18EA0523#14F001\n# ```\n")
    text = markdown(report(capture))
    table_row = next(line for line in text.splitlines() if "&#124;" in line)
    assert "can&#124;&#60;script&#62;" in table_row
    assert "<script>" not in table_row
    assert "\n<script>" not in text
