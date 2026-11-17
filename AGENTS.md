# Codex instructions — Siren 3 Mapper

## Mission
Build a local-first Python CLI for non-destructive, black-box characterization of an owned Siren 3 Pro on an isolated NMEA 2000 bench. Map what is observed, not what is assumed. The independent vessel monitoring project must not depend on this project.

## Scope and safety
- Default to offline capture replay. Never open a physical CAN interface or transmit unless a user explicitly selects a hardware mode and interface.
- Default hardware interfaces to listen-only. Active CAN transmission requires an explicit CLI flag, a verified 250000-bit/s isolated bench, correct termination, and source-address claiming with collision handling.
- No destructive probing, credential extraction, firmware dumping, cloud login, pairing, factory reset, or uncontrolled alarm injection without a separate explicit request.
- Rate-limit active scenarios; support an immediate stop; never send on an operational boat network.
- Never treat candump TX/RX markings alone as proof of a remote device response. Correlate by source address, PGN, timestamps, and controlled experiments.
- Keep raw evidence immutable. Clearly tag decoded facts, hypotheses, and unknowns. Keep capture timestamps and provenance.

## Implementation conventions
- Python 3.11+ target for development, typed code, pytest, ruff. Minimize dependencies; `python-can` for SocketCAN, standard library for persistence initially.
- Repo has a CLI (`siren-mapper`), offline fixtures, protocol decoder, discovery parser, test runner, and export. SQLite optional later.
- Use existing NMEA 2000 simulator as a separate process or adapter; do not copy/reimplement its existing functionality until reviewing its repository.
- Prefer small documented PRs, deterministic tests, and no speculative UI.
- Human live/replay output and structured evidence use the shared `PacketDecoder`,
  PGN registry, and reassembler. New formatters must not introduce CAN transmission.
- Keep validated fields separate from partial malformed-message fields; never turn
  unknown/proprietary bytes into speculative text or device-processing claims.
- Verify all CAN identifiers with 29-bit extended CAN bitfields; ISO requests and fast-packet parsing require tests.
- Read `PLAN.md` and `docs/known-findings.md` before implementation. Work one phase at a time.
- For protocol decoding, use `.agents/skills/nmea2000-protocol-analysis/SKILL.md`; for transmission tests use `can-experiment-design`; for analyzing capture files use `can-capture-analysis`.

## Definition of done
Run lint and automated tests, update the finding ledger with source evidence, document how to reproduce, and disclose any skipped hardware tests. Do not claim successful device interaction without real capture evidence.
