# Phase 0 inventory — 2026-11-16

## Boundaries and environment

User-reported isolated bench: Linux SocketCAN, physical `can1`, virtual `vcan0`,
250000 bit/s; apparent Siren source 5 (`0x05`), simulator source 35 (`0x23`).
These are capture provenance and intended bench settings, not measurements made
by this implementation. No interface was opened, configured or queried for
live traffic. Development runtime: Python 3.11.2 on Linux
6.18.58.1-microsoft-standard-WSL2. Bench termination and current link state
remain unverified. No hardware mode exists in phases 0–1.

## Existing simulator review

Reviewed local `/home/parrot/code/nmea2k-sim`: README, pyproject.toml,
requirements.txt, and `src/nmea_sim/{can_id,fast_packet,device_protocol,receive_transport}.py`.

- Typed Python 3.11 package `nmea_sim`; runtime dependency python-can 4.6.1,
  pytest test extra and optional PySide6 dashboard. requirements.txt also contains
  a pinned editable checkout and GUI dependencies; it is not a suitable mapper dependency.
- `can_id.py` owns identifier compose/extract. `fast_packet.py` owns bounded RX
  reassembly and TX fragmentation. `device_protocol.py` owns discovery codecs.
- Simulator/device modules own address claiming and collision handling; transport
  opens CAN, and receive transport also supports socketcand. These are separate
  from wire decoding but importing adapters can introduce hardware dependencies.
- Product decoder allows 132/134-byte variants; heartbeat decoder requires eight
  bytes. The observed Siren product is 134 bytes and heartbeat snippet is seven.

Decision: keep simulator as a separate process for future phases, with no mapper
runtime dependency or imports. Mapper implements a small offline reader and
strict discovery decoder after this review; no simulator scenarios, TX encoders,
address manager or sensor simulation were copied. Shared-code extraction is
not needed for these phases and should be evaluated separately if both projects
need a maintained common codec. The independent monitoring project gains no
dependency on this repository.

## Reproduction and references

Use README commands and fixtures/README.md inventory. Fixtures were transcribed
from the existing findings ledger, not collected during implementation.

Layouts were checked against the primary CANboat project documentation:
https://canboat.github.io/canboat/canboat.html (accessed 2026-11-16), identifier
header, packet framing, and PGNs 59392, 59904, 60928, 126464, 126993, 126996,
126998. This reverse-engineered reference is not certification or a purchased
NMEA normative specification. Strict ordered assembly is an explicit mapper
policy; out-of-order senders are rejected rather than heuristically reconstructed.
