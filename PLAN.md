# Siren 3 Mapper — phased implementation plan

## Goal
A repeatable Python CLI that ingests SocketCAN candump logs, decodes NMEA 2000 fast packets, inventories device identity and supported PGNs, and correlates controlled CAN stimuli with responses from an owned Siren 3 Pro. It is a **mapping tool**, not a replacement for the independent vessel monitoring system.

## Phase 0 — Inventory and boundaries (0.5–1 day)
- Inspect existing `nmea2k-sim` repository, architecture, dependencies and NMEA codec modules before proposing shared code.
- Capture environment: Linux SocketCAN, physical `can1`, virtual `vcan0`, 250 kbit/s, source IDs Siren 5 and simulator 35.
- Add sample captures in `fixtures/` without secrets or unrelated devices.
- Acceptance: documented CLI/test commands, fixture inventory, no live interface opened by default.

## Phase 1 — Offline decoder MVP (1–2 days)
- Parse candump formats including `can1 1DF01105#...` and `candump -L` timestamps, `T`/`R` flags when present.
- Decode extended 29-bit CAN identifier, PGN/priority/source/destination.
- Assemble NMEA 2000 fast-packet frames by source, PGN, sequence and time window; validate length and frame ordering, handle missing frames.
- Decode PGN 60928, 59904, 59392, 126993, 126996, 126464, 126998 acknowledgement cases.
- Acceptance: offline fixture reconstructs 20-frame Product Information response and two PGN lists; returns model `Siren 3 Pro`, software `1.0.0`, serial `21003942`; rejects malformed packets.

## Phase 2 — CLI and evidence reports (1–2 days)
- Commands: `decode <log>`, `inventory <log>`, `diff <baseline> <stimulus>`, `report <log> --format markdown|json`.
- Emit structured evidence records: timestamp, CAN ID, PGN, source/destination, decoded fields, provenance, confidence, unresolved questions.
- Acceptance: deterministic JSON and Markdown reports; no hardware requirement; captured source data retained untouched.

## Phase 3 — Live passive capture (0.5–1 day)
- Add explicit `capture --channel can1 --listen-only` mode, interface checks, graceful stop and logs.
- Avoid reconfiguring interfaces automatically without user opt-in.
- Acceptance: tests with vcan, plus user-run isolated-bench smoke test; passive mode never transmits.

## Phase 4 — Active discovery (1–2 days)
- Integrate with existing simulator's source-address management; do not send from unclaimed address.
- Query Product Information (126996), Configuration (126998), TX/RX PGN List (126464); document NACK/timeouts.
- Acceptance: explicit `--allow-transmit`, bounded request count and intervals, proper address management, vcan tests, reproducible capture compared against baseline.

## Phase 5 — Controlled sensor experiments (2–3 days)
- Reuse simulator for PGN 127488, 127489 and 127505; create normal-to-abnormal scenarios with conservative bounds and single-variable changes.
- Baseline capture, stimulus, recovery, and post-stimulus capture. Record sent frames, observation window and all Siren-originating traffic.
- Acceptance: scenario result labels visible change/no visible change/inconclusive; never infer internal processing solely from silence.

## Later / optional
- BLE and Wi-Fi advertisement correlation as a separate passive module; physical LED observation worksheet; richer time-series dashboard only after CLI evidence quality is established.

## Risks / guardrails
- Physical bus ACK and local TX/RX echo are not proof of Siren application processing.
- Siren backup battery can invalidate apparent power-cycle tests.
- Device PGN lists describe advertised capability, not necessarily active configuration.
- Proprietary or reserved bytes remain undecoded unless supported by evidence.

## Original first-task note
Superseded by the user request to implement phases 0 and 1. Original scope: implement Phase 1 only. Review AGENTS.md and docs/known-findings.md. Scaffold typed Python package and pytest fixtures; no live CAN connections or transmission. Return test commands, decoded product fields, and open protocol questions.

## Implementation status — 2026-11-16

- Phase 0 complete: simulator architecture/dependency review and reported bench
  boundaries in `docs/phase-0-inventory.md`; transcribed, hashed fixtures and
  provenance in `fixtures/README.md`; commands in `README.md`. No live verification.
- Phase 1 complete: offline CLI, strict candump reader, 29-bit identifiers, bounded
  fast-packet assembly, discovery decoders, positive and malformed regression tests.
  Product and both PGN-list acceptance vectors reproduce. Seven-byte heartbeat
  remains an explicit length anomaly; successful configuration decoding has only
  synthetic coverage, while the observed configuration NACK is reproduced.
- Phase 2 complete: offline inventory/diff/report commands, versioned structured
  evidence with timestamp, CAN ID, source/destination, decoded facts, file/line/hash
  provenance, confidence and unresolved questions. Deterministic JSON/Markdown
  preserves raw frames and errors, including incomplete packets. Comparison reports
  observational differences without claiming stimulus causality. See
  `docs/evidence-reports.md` for commands and limits.
- Phase 3 software implemented: explicit passive SocketCAN capture, fail-closed
  interface checks, exclusive evidence logs/metadata, bounded duration/count,
  SIGINT/SIGTERM cleanup and real vcan validation. User subsequently reported that
  passive physical `can1` capture worked after enabling kernel listen-only; raw
  capture/electrical-silence evidence has not been reviewed here.
- Phase 4 software implemented: explicit `discover --allow-transmit`, reuse of the
  simulator address manager, bounded claims and three ISO requests, candidate
  response/NACK/timeout evidence, immutable raw log/sidecar, baseline and recovery,
  collision handling and real vcan validation. See `docs/active-discovery.md`.
  Physical active discovery remains untested; no agent-opened physical interface.
- Phase 5 and later remain pending. No sensor stimulus scenarios or inferred
  Siren application-processing results have been implemented.
