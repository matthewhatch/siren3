"""Deterministic inventory, observation comparison and evidence rendering."""

import json
from collections import Counter
from typing import Any


def _unique(values: list[Any]) -> list[Any]:
    by_json = {json.dumps(value, sort_keys=True): value for value in values}
    return [by_json[key] for key in sorted(by_json)]


def inventory(capture: dict[str, Any]) -> dict[str, Any]:
    sources = sorted({(f["channel"], f["source"]) for f in capture["raw_frames"]})
    entries = []
    for channel, source in sources:
        frames = [
            f for f in capture["raw_frames"] if (f["channel"], f["source"]) == (channel, source)
        ]
        records = [
            r for r in capture["records"] if (r["channel"], r["source"]) == (channel, source)
        ]
        observations = []
        for record in records:
            if record["status"] == "decoded" and record["pgn"] in (60928, 126996, 126464):
                observations.append(
                    {
                        "evidence_id": record["evidence_id"],
                        "pgn": record["pgn"],
                        "decoded": record["decoded"],
                        "provenance": record["provenance"],
                    }
                )
        entries.append(
            {
                "channel": channel,
                "source": source,
                "frame_count": len(frames),
                "message_count": len(records),
                "observed_pgns": sorted({f["pgn"] for f in frames}),
                "observations": observations,
                "unresolved_questions": [
                    "Source address is a capture grouping, not a verified persistent device identity.",
                    "Advertised PGN lists describe capability; active processing remains unknown.",
                ],
            }
        )
    return {
        "schema_version": 1,
        "capture": capture["capture"],
        "sources": entries,
        "evidence": capture["records"],
        "raw_frames": capture["raw_frames"],
        "errors": capture["errors"],
        "unresolved_questions": capture["unresolved_questions"],
    }


def _streams(capture: dict[str, Any]) -> dict[tuple[str, int, int, int], dict[str, Any]]:
    streams: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    for frame in capture["raw_frames"]:
        key = (
            frame["channel"],
            frame["source"],
            -1 if frame["destination"] is None else frame["destination"],
            frame["pgn"],
        )
        stream = streams.setdefault(key, {"frame_count": 0, "messages": []})
        stream["frame_count"] += 1
    for record in capture["records"]:
        key = (
            record["channel"],
            record["source"],
            -1 if record["destination"] is None else record["destination"],
            record["pgn"],
        )
        streams[key]["messages"].append(record)
    return streams


def _summary(stream: dict[str, Any] | None) -> dict[str, Any]:
    if stream is None:
        return {
            "frame_count": 0,
            "message_count": 0,
            "payloads": [],
            "payload_counts": {},
            "statuses": {},
        }
    messages = stream["messages"]
    return {
        "frame_count": stream["frame_count"],
        "message_count": len(messages),
        "payloads": sorted({m["payload_hex"] for m in messages}),
        "payload_counts": dict(sorted(Counter(m["payload_hex"] for m in messages).items())),
        "statuses": dict(sorted(Counter(m["status"] for m in messages).items())),
    }


def compare(baseline: dict[str, Any], stimulus: dict[str, Any]) -> dict[str, Any]:
    left, right = _streams(baseline), _streams(stimulus)
    changes = []
    for key in sorted(left.keys() | right.keys()):
        before, after = _summary(left.get(key)), _summary(right.get(key))
        if before == after:
            continue
        channel, source, destination, pgn = key
        changes.append(
            {
                "channel": channel,
                "source": source,
                "destination": None if destination == -1 else destination,
                "pgn": pgn,
                "change": "added"
                if key not in left
                else "removed"
                if key not in right
                else "changed",
                "baseline": before,
                "stimulus": after,
                "baseline_evidence": [
                    r["evidence_id"] for r in left.get(key, {}).get("messages", [])
                ],
                "stimulus_evidence": [
                    r["evidence_id"] for r in right.get(key, {}).get("messages", [])
                ],
            }
        )
    errors = {"baseline": baseline["errors"], "stimulus": stimulus["errors"]}
    return {
        "schema_version": 1,
        "baseline": baseline["capture"],
        "stimulus": stimulus["capture"],
        "result": "inconclusive"
        if any(errors.values())
        else "observed_change"
        if changes
        else "no_observed_change",
        "changes": changes,
        "errors": errors,
        "evidence": {"baseline": baseline["records"], "stimulus": stimulus["records"]},
        "raw_frames": {"baseline": baseline["raw_frames"], "stimulus": stimulus["raw_frames"]},
        "comparison_policy": (
            "Compare per-channel/source/destination/PGN frame and message counts, complete "
            "payload counts, and statuses. Ignore timestamps, direction, priority, fast-packet "
            "sequence and message order. No rates or causal response are inferred."
        ),
        "unresolved_questions": [
            "Are collection durations and bench conditions comparable?",
            "Does each source address belong to the same device in both captures?",
            "Do differences reflect a controlled stimulus, local echo, background traffic or loss?",
            "Unchanged observations do not establish that a device ignored or processed a stimulus.",
        ],
    }


def report(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        **capture,
        "inventory": inventory(capture)["sources"],
        "summary": {
            "raw_frame_count": len(capture["raw_frames"]),
            "message_count": len(capture["records"]),
            "error_count": len(capture["errors"]),
            "statuses": dict(sorted(Counter(r["status"] for r in capture["records"]).items())),
        },
        "unresolved_questions": _unique(
            capture["unresolved_questions"]
            + [q for r in capture["records"] for q in r["unresolved_questions"]]
        ),
    }


def markdown(document: dict[str, Any]) -> str:
    """Indent serialized evidence so capture text cannot introduce Markdown syntax."""
    summary = document["summary"]
    lines = [
        "# Siren Mapper evidence report",
        "",
        (
            f"Frames: {summary['raw_frame_count']}; messages: {summary['message_count']}; "
            f"errors: {summary['error_count']}."
        ),
        "",
        (
            "Results describe offline observations. Source ownership and application behavior "
            "remain unverified."
        ),
        "",
        "## Source inventory",
        "",
        "| Channel | Source | Frames | Messages | Observed PGNs |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in document["inventory"]:
        # Encode arbitrary channel characters without allowing table/HTML injection.
        channel = "".join(
            c if c.isalnum() or c in "_-" else f"&#{ord(c)};" for c in item["channel"]
        )
        lines.append(
            f"| {channel} | {item['source']} | {item['frame_count']} | "
            f"{item['message_count']} | {', '.join(map(str, item['observed_pgns']))} |"
        )
    lines += [
        "",
        "## Message evidence",
        "",
        "| Evidence | PGN | Source | Status | Confidence | Capture lines |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for record in document["records"]:
        lines.append(
            f"| {record['evidence_id']} | {record['pgn']} | {record['source']} | "
            f"{record['status']} | {record['confidence']['level']} | "
            f"{', '.join(map(str, record['provenance']['lines']))} |"
        )
    lines += [
        "",
        "## Evidence, provenance and unresolved questions",
        "",
        (
            "The complete structured evidence below includes raw frames, decoded facts, "
            "confidence, anomalies and file hashes."
        ),
        "",
    ]
    lines.extend(
        "    " + line for line in json.dumps(document, sort_keys=True, indent=2).splitlines()
    )
    return "\n".join(lines) + "\n"
