"""Versioned, evidence-backed device fingerprints from the shared offline decoder."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .evidence import read_capture

DISCOVERY_PGNS = (60928, 126996, 126998, 126464)


def reference(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": record["evidence_id"],
        "timestamp": record["timestamp"],
        "can_id": record["can_id"],
        "provenance": record["provenance"],
        "payload_hex": record["payload_hex"],
    }


def _section(observations: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = {
        json.dumps(item["record"]["decoded"], sort_keys=True): item["record"]["decoded"]
        for item in observations
        if item["record"]["pgn"] != 59392
    }
    acknowledgements = [item for item in observations if item["record"]["pgn"] == 59392]
    negatives = {
        item["record"]["decoded"]["result"] for item in acknowledgements if item["request"]
    }
    if (
        len(payloads) > 1
        or len(negatives) > 1
        or (payloads and negatives & {"nack", "busy", "access_denied"})
    ):
        status = "conflicting_observations"
    elif any(r.get("session_status", "").startswith("inconclusive") for r in requests):
        status = "inconclusive_request_window"
    elif payloads:
        status = (
            "response_observed"
            if any(item["request"] for item in observations)
            else "observed_without_request"
        )
    elif "nack" in negatives:
        status = "request_not_accepted"
    elif negatives:
        status = "acknowledgement_observed"
    elif acknowledgements:
        status = "uncorrelated_acknowledgement_observed"
    elif requests:
        status = (
            "timeout" if all(r["status"] == "timeout" for r in requests) else "no_response_observed"
        )
    else:
        status = "not_observed"
    return {
        "status": status,
        "fields": next(iter(payloads.values())) if len(payloads) == 1 else None,
        "alternatives": [payloads[key] for key in sorted(payloads)] if len(payloads) > 1 else [],
        "observations": observations,
        "requests": requests,
    }


def build_profile(
    capture: dict[str, Any],
    *,
    target: int | None = None,
    response_window: float = 2,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Do not merge source-only epochs across captures or infer a device from a TX destination."""
    if target is not None and not 0 <= target <= 251:
        raise ValueError("target must be 0–251")
    if not 0 < response_window <= 10:
        raise ValueError("response window must be in (0, 10] seconds")
    devices: dict[str, dict[str, Any]] = {}
    owners: dict[tuple[str, int], str] = {}
    owner_at_line: dict[int, str] = {}
    target_at_request: dict[int, str | None] = {}
    records = capture["records"]
    single = {r["frames"][0]["line"]: r for r in records if len(r["frames"]) == 1}
    transitions = []

    def node(
        key: str, channel: str, source: int, name: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if key not in devices:
            devices[key] = {
                "device_id": key,
                "identity": {
                    "name_hex": name["name_hex"] if name else None,
                    "fields": name,
                    "confidence": "observed_name_unverified" if name else "unknown",
                },
                "locations": [],
                "observed_pgns": [],
                "evidence": [],
                "requests": [],
                "unresolved_questions": [
                    "NAME is an observed identity claim, not independently verified ownership.",
                    "Advertised PGNs do not establish measurement processing or alarm support.",
                ],
            }
        device = devices[key]
        location = {"channel": channel, "source": source}
        if location not in device["locations"]:
            device["locations"].append(location)
        return device

    for frame in capture["raw_frames"]:
        channel, source, line = frame["channel"], frame["source"], frame["line"]
        if source > 251:
            continue  # null/global/cannot-claim sources are not address-owning device nodes
        address = (channel, source)
        record = single.get(line)
        if (
            record
            and record["status"] == "decoded"
            and record["pgn"] == 60928
            and record["destination"] == 255
        ):
            fields = record["decoded"]
            key = "name:" + fields["name_hex"]
            previous = owners.get(address)
            if previous and previous != key:
                transitions.append(
                    {
                        "kind": "address_identity_changed",
                        "channel": channel,
                        "source": source,
                        "before": previous,
                        "after": key,
                        "evidence": reference(record),
                    }
                )
            for old_address, old_key in list(owners.items()):
                if old_key == key and old_address != address:
                    del owners[old_address]
                    transitions.append(
                        {
                            "kind": "name_location_changed_or_duplicate",
                            "before": list(old_address),
                            "after": list(address),
                            "name_hex": fields["name_hex"],
                            "evidence": reference(record),
                        }
                    )
            owners[address] = key
            node(key, channel, source, fields)
        if address not in owners:
            owners[address] = f"source:{channel}:{source}:line:{line}"
        key = owners[address]
        device = node(key, channel, source)
        device["observed_pgns"].append(frame["pgn"])
        device["current_location"] = {"channel": channel, "source": source}
        if "first_timestamp" not in device:
            device["first_timestamp"] = frame["timestamp"]
        device["last_timestamp"] = frame["timestamp"]
        device["frame_count"] = device.get("frame_count", 0) + 1
        owner_at_line[line] = key
        if record and record["status"] == "decoded" and record["pgn"] == 59904:
            target_at_request[line] = owners.get((channel, record["destination"]))

    record_owner: dict[str, str] = {}
    for record in records:
        keys = {owner_at_line.get(frame["line"]) for frame in record["frames"]}
        if len(keys) == 1 and None not in keys:
            key = keys.pop()
            devices[key]["evidence"].append(record)
            record_owner[record["evidence_id"]] = key
        elif record["source"] <= 251:
            key = f"uncertain:{record['channel']}:{record['source']}:line:{record['frames'][0]['line']}"
            node(key, record["channel"], record["source"])["evidence"].append(record)
            devices[key]["unresolved_questions"].append(
                "Identity changed during this logical message."
            )
            record_owner[record["evidence_id"]] = key

    requests = []
    for record in records:
        if (
            record["status"] == "decoded"
            and record["pgn"] == 59904
            and record["decoded"]["requested_pgn"] in DISCOVERY_PGNS
            and record["destination"] is not None
            and record["destination"] <= 251
        ):
            key = target_at_request.get(record["frames"][0]["line"])
            requests.append(
                {
                    "pgn": record["decoded"]["requested_pgn"],
                    "source": record["source"],
                    "target": record["destination"],
                    "channel": record["channel"],
                    "request": reference(record),
                    "target_name": key[5:] if key and key.startswith("name:") else None,
                    "device_id": key,
                    "status": "no_response_observed",
                    "observations": [],
                }
            )
    session_requests = {r["request_line"]: r for r in (session or {}).get("requests", [])}
    unexpected = []
    observations: dict[str, list[dict[str, Any]]] = {key: [] for key in devices}
    seen: dict[tuple[Any, ...], str] = {}
    for record in records:
        if record["status"] != "decoded" or record["pgn"] not in (*DISCOVERY_PGNS, 59392):
            continue
        key = record_owner.get(record["evidence_id"])
        if key is None:
            continue
        pgn = record["decoded"]["requested_pgn"] if record["pgn"] == 59392 else record["pgn"]
        first_line = record["frames"][0]["line"]
        prior = [
            r
            for r in requests
            if r["channel"] == record["channel"]
            and r["target"] == record["source"]
            and r["request"]["provenance"]["lines"][-1] < first_line
        ]
        req = prior[-1] if prior else None
        reason = None
        confidence = "capture_order_only_timing_unknown"
        if req is None or req["pgn"] != pgn:
            reason = "no matching current captured request"
        elif record["destination"] not in (None, 255, req["source"]):
            reason = "reply destination does not match requester"
        elif record["pgn"] == 59392 and record["destination"] != req["source"]:
            reason = "ISO acknowledgement is not directed to requester"
        elif req["device_id"] and req["device_id"].startswith("name:") and req["device_id"] != key:
            reason = "target NAME changed after request"
        else:
            live = session_requests.get(req["request"]["provenance"]["lines"][0])
            if live is not None and "observations" in live:
                candidates = {tuple(o["lines"]) for o in live["observations"]}
                if tuple(record["provenance"]["lines"]) not in candidates:
                    reason = "not qualified in the recorded live request window"
                else:
                    confidence = "recorded_monotonic_request_window"
            else:
                start, end = req["request"]["timestamp"], record["timestamp"]
                if start is not None and end is not None:
                    delta = Decimal(end) - Decimal(start)
                    first_stamp = record["frames"][0]["timestamp"]
                    first_delta = (
                        Decimal(first_stamp) - Decimal(start) if first_stamp is not None else delta
                    )
                    if first_delta < 0 or delta < 0 or delta > Decimal(str(response_window)):
                        reason = "outside captured request time window"
                    else:
                        confidence = "source_pgn_destination_and_time_window"
        duplicate_key = (
            key,
            record["pgn"],
            record["payload_hex"],
            req["request"]["evidence_id"] if req and reason is None else None,
        )
        observation = {
            "record": record,
            "request": req["request"] if req and reason is None else None,
            "request_target_name": req["target_name"] if req else None,
            "target_name": devices[key]["identity"]["name_hex"],
            "correlation": confidence if reason is None else "uncorrelated",
            "origin_hint": "local_origin"
            if any(f["direction"] == "T" for f in record["frames"])
            else "unverified",
            "duplicate_of": seen.get(duplicate_key),
            "unresolved_question": "Temporal correlation does not prove sender identity or internal processing.",
        }
        seen[duplicate_key] = record["evidence_id"]
        observations[key].append(observation)
        if reason is not None and record["pgn"] != 60928:
            unexpected.append({"evidence": reference(record), "reason": reason})
        elif reason is None and req is not None:
            req["observations"].append(reference(record))
            req["device_id"] = key
            req["status"] = (
                "response_observed" if record["pgn"] != 59392 else record["decoded"]["result"]
            )

    timestamps = [
        Decimal(f["timestamp"]) for f in capture["raw_frames"] if f["timestamp"] is not None
    ]
    end = max(timestamps) if timestamps else None
    record_index = {r["evidence_id"]: r for r in records}
    for req in requests:
        replies = [record_index[o["evidence_id"]] for o in req["observations"]]
        codes = {r["decoded"]["result"] for r in replies if r["pgn"] == 59392}
        has_content = any(r["pgn"] != 59392 for r in replies)
        if len(codes) > 1 or (has_content and codes & {"nack", "busy", "access_denied"}):
            req["status"] = "conflicting_observations"
        line = req["request"]["provenance"]["lines"][0]
        live = session_requests.get(line)
        if live:
            req["session_status"] = live["status"]
        if not req["observations"]:
            start = req["request"]["timestamp"]
            if live and live["status"] != "pending":
                req["status"] = live["status"]
            elif (
                start is not None
                and end is not None
                and end - Decimal(start) >= Decimal(str(response_window))
            ):
                req["status"] = "timeout"
        if req["device_id"] in devices:
            devices[req["device_id"]]["requests"].append(req)

    for key, device in devices.items():
        device["observed_pgns"] = sorted(set(device["observed_pgns"]))
        owned = observations[key]
        for field, pgn in (("product", 126996), ("configuration", 126998)):
            selected = [
                o
                for o in owned
                if o["record"]["pgn"] == pgn
                or (o["record"]["pgn"] == 59392 and o["record"]["decoded"]["requested_pgn"] == pgn)
            ]
            device[field] = _section(selected, [r for r in device["requests"] if r["pgn"] == pgn])
        lists = {}
        for role in ("transmit", "receive"):
            selected = [
                o
                for o in owned
                if o["record"]["pgn"] == 126464 and o["record"]["decoded"]["function"] == role
            ]
            section = _section(selected, [r for r in device["requests"] if r["pgn"] == 126464])
            section["pgns"] = section["fields"]["pgns"] if section["fields"] else None
            lists[role] = section
        device["advertised_pgns"] = lists
        claims = [o for o in owned if o["record"]["pgn"] == 60928]
        device["identity"]["claims"] = claims
    selected_devices = [
        d
        for _, d in sorted(devices.items())
        if target is None or any(location["source"] == target for location in d["locations"])
    ]
    return {
        "schema_version": 1,
        "kind": "nmea2000_device_profiles",
        "capture": capture["capture"],
        "target": target,
        "session": (
            {
                key: session[key]
                for key in (
                    "mode",
                    "started_utc",
                    "ended_utc",
                    "stop_reason",
                    "elapsed_seconds",
                    "limits",
                    "duration_limit_seconds",
                    "count_limit",
                    "hardware_selected",
                    "isolated_bench_attested",
                    "identity_status",
                    "iso_name",
                    "manufacturer_code",
                    "allow_unassigned_bench_identity",
                    "frames",
                    "tx_frames",
                )
                if key in session
            }
            if session is not None
            else None
        ),
        "devices": selected_devices,
        "requests": requests,
        "identity_transitions": transitions,
        "unexpected_observations": unexpected,
        "errors": capture["errors"],
        "raw_frames": capture["raw_frames"],
        "unresolved_questions": [
            "Source addresses are mutable; NAME claims are not independently authenticated.",
            "Timestamp-free snippet order cannot prove request timing or causality.",
            "Missing responses do not imply an unsupported capability.",
        ],
    }


def profile_from_capture(
    path: Path,
    *,
    target: int | None = None,
    response_window: float = 2,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    capture = read_capture(path)
    sidecar = Path(str(path) + ".json")
    if session is None and sidecar.exists():
        candidate = json.loads(sidecar.read_text())
        if isinstance(candidate, dict) and candidate.get("mode") in ("active_discovery", "passive"):
            if candidate.get("sha256") != capture["capture"]["sha256"]:
                raise ValueError("discovery sidecar hash does not match raw capture")
            session = candidate
            response_window = candidate.get("limits", {}).get("response_window", response_window)
    return build_profile(capture, target=target, response_window=response_window, session=session)


def profile_markdown(profile: dict[str, Any]) -> str:
    lines = [
        "# NMEA 2000 device discovery",
        "",
        "Observed claims and candidate replies; internal processing remains unverified.",
        "",
    ]
    for device in profile["devices"]:
        identity = device["identity"]
        lines += [
            f"## Device {json.dumps(identity['name_hex'] or 'unknown NAME')}",
            "",
            "Locations:",
            "",
            "    " + json.dumps(device["locations"], sort_keys=True),
            "",
            "Identity confidence: " + identity["confidence"],
            "",
        ]
        for title, field in (
            ("Product Information", "product"),
            ("Configuration Information", "configuration"),
        ):
            section = device[field]
            lines += [f"### {title}", "", f"Status: {section['status'].replace('_', ' ')}.", ""]
            if section["fields"]:
                lines.extend(
                    "    " + text
                    for text in json.dumps(section["fields"], sort_keys=True, indent=2).splitlines()
                )
                lines.append("")
        for role in ("transmit", "receive"):
            section = device["advertised_pgns"][role]
            lines += [
                f"Advertised {role} PGNs: "
                + (
                    ", ".join(map(str, section["pgns"]))
                    if section["pgns"] is not None
                    else "not observed / conflicting; inspect evidence"
                ),
                "",
            ]
    lines += ["## Complete evidence and unresolved questions", ""]
    lines.extend(
        "    " + text for text in json.dumps(profile, sort_keys=True, indent=2).splitlines()
    )
    return "\n".join(lines) + "\n"


def save_profile(profile: dict[str, Any], path: Path) -> None:
    with (
        path.open("x", encoding="utf-8") as output,
        Path(str(path) + ".md").open("x", encoding="utf-8") as markdown,
    ):
        output.write(json.dumps(profile, sort_keys=True, indent=2) + "\n")
        markdown.write(profile_markdown(profile))


def load_profile(path: Path) -> dict[str, Any]:
    profile = json.loads(path.read_text())
    if (
        not isinstance(profile, dict)
        or profile.get("kind") != "nmea2000_device_profiles"
        or profile.get("schema_version") != 1
    ):
        raise ValueError("expected a version-1 NMEA device profile")
    if not isinstance(profile.get("devices"), list):
        raise ValueError("device profile must contain a devices list")  # noqa: TRY004
    for device in profile["devices"]:
        if (
            not isinstance(device, dict)
            or not isinstance(device.get("identity"), dict)
            or not isinstance(device.get("locations"), list)
            or not device["locations"]
            or not isinstance(device.get("device_id"), str)
            or not isinstance(device.get("observed_pgns"), list)
            or not {"name_hex", "confidence"} <= device["identity"].keys()
            or not all(
                isinstance(device.get(key), dict)
                for key in ("product", "configuration", "advertised_pgns")
            )
        ):
            raise ValueError("malformed device profile entry")
        sections = [device["product"], device["configuration"]]
        if not {"transmit", "receive"} <= device["advertised_pgns"].keys():
            raise ValueError("device profile must separate transmit and receive PGN lists")
        sections.extend(device["advertised_pgns"].values())
        if any(
            not isinstance(s, dict) or not {"status", "fields", "observations"} <= s.keys()
            for s in sections
        ):
            raise ValueError("malformed profile observation section")
    return profile


def diff_profiles(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    left = {d["identity"]["name_hex"]: d for d in before["devices"] if d["identity"]["name_hex"]}
    right = {d["identity"]["name_hex"]: d for d in after["devices"] if d["identity"]["name_hex"]}
    matches = []
    for name in sorted(left.keys() & right.keys()):
        a, b = left[name], right[name]
        changes = {}

        def semantic(section: dict[str, Any]) -> dict[str, Any]:
            fields = section["fields"]
            if fields is not None and "pgns" in fields:
                fields = {**fields, "pgns": sorted(fields["pgns"])}
            return {
                "fields": fields,
                "status": section["status"],
                "alternatives": section.get("alternatives", []),
                "acknowledgements": sorted(
                    {
                        (o["record"]["decoded"]["control"], o["record"]["decoded"]["requested_pgn"])
                        for o in section["observations"]
                        if o["record"]["pgn"] == 59392
                    }
                ),
            }

        for key in ("product", "configuration", "advertised_pgns"):
            if key == "advertised_pgns":
                av = {r: semantic(v) for r, v in a[key].items()}
                bv = {r: semantic(v) for r, v in b[key].items()}
            else:
                av, bv = semantic(a[key]), semantic(b[key])
            if av != bv:
                changes[key] = {"before": av, "after": bv}
        matches.append(
            {
                "name_hex": name,
                "address_changed": a.get("current_location", a["locations"][-1])
                != b.get("current_location", b["locations"][-1]),
                "location_history_changed": a["locations"] != b["locations"],
                "before_locations": a["locations"],
                "after_locations": b["locations"],
                "content_changes": changes,
            }
        )
    return {
        "schema_version": 1,
        "matched_by_observed_name": matches,
        "unmatched_before": [
            d["device_id"]
            for d in before["devices"]
            if not d["identity"]["name_hex"] or d["identity"]["name_hex"] not in right
        ],
        "unmatched_after": [
            d["device_id"]
            for d in after["devices"]
            if not d["identity"]["name_hex"] or d["identity"]["name_hex"] not in left
        ],
        "unresolved_questions": [
            "Matching NAME claims is not independent identity verification; unknown source-only nodes are never matched across runs."
        ],
    }
