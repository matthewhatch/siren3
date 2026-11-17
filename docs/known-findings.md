# Siren 3 Pro capture facts — 2026-11-16

Source: user-provided CAN captures from isolated physical NMEA 2000 bench, `can1`, 250 kbit/s. Captures are snippets, not a complete machine-readable session file.

- Siren apparent source address `0x05`; its heartbeat CAN ID `0x1DF01105` = PGN 126993. Example `1DF01105#60EAE10C010000`, recurring near 60 seconds. Observed data length 7.
- Simulator source 35 / `0x23`, NAME `c0648c00ffd89fe6`; manual claim `18EEFF23#E69FD8FF008C64C0`. Proper simulator address management is preferred for active tests.
- Product Information request `18EA0523#14F001` yielded 20 fast-packet CAN frames `19F01405#...`:
```
19F01405#00861C0C73365369
19F01405#0172656E20332050
19F01405#02726F00FFFFFFFF
19F01405#03FFFFFFFFFFFFFF
19F01405#04FFFFFFFFFFFFFF
19F01405#05FFFF312E302E30
19F01405#0600FFFFFFFFFFFF
19F01405#07FFFFFFFFFFFFFF
19F01405#08FFFFFFFFFFFFFF
19F01405#09FFFFFFFFFFFF00
19F01405#0AFFFFFFFFFFFFFF
19F01405#0BFFFFFFFFFFFFFF
19F01405#0CFFFFFFFFFFFFFF
19F01405#0DFFFFFFFFFFFFFF
19F01405#0EFFFFFF32313030
19F01405#0F3339343200FFFF
19F01405#10FFFFFFFFFFFFFF
19F01405#11FFFFFFFFFFFFFF
19F01405#12FFFFFFFFFFFFFF
19F01405#130201FFFFFFFFFF
```
- Decoded Product: NMEA DB version 3100; product code 13939; model `Siren 3 Pro`; software `1.0.0`; serial `21003942`; certification level 2; load equivalency 1. Model version not supplied.
- Configuration request `18EA0523#16F001` produced negative ACK `18E82305#01FFFFFFFF16F001`.
- PGN list request `18EA0523#00EE01` returned two lists:
```
19EEFF05#001C0000E80000EA
19EEFF05#010000EB0000EC00
19EEFF05#0200EE0000ED0100
19EEFF05#03EE0111F00114F0
19EEFF05#0401FFFFFFFFFFFF
01EEFF05#001F0100E80000EA
01EEFF05#010000EB0000EC00
01EEFF05#0200EE00D8FE0000
01EEFF05#03ED0100F20101F2
01EEFF05#040111F201FFFFFF
```
- Advertised transmit PGNs: 59392, 59904, 60160, 60416, 60928, 126208, 126464, 126993, 126996.
- Advertised receive PGNs: 59392, 59904, 60160, 60416, 60928, 65240, 126208, 127488, 127489, 127505.
- Siren address claim `18EEFF05#FFFF5F8100AB8CC0`.
- Engine rapid update PGN 127488 transmitted at RPM 0/700/1200/3000/5000, with no additional Siren CAN response observed in short sample.
- Null-address ISO Request `18EA23FE#00EE00` observed following unclaimed simulator messages; request origin not proven.
- No Siren BLE advertiser conclusively identified. Wi-Fi untested. LEDs quiet while CAN heartbeat persisted; unit may have backup power.

Treat the raw CAN lines as authoritative observations. Decode derived details under tests; treat unverified behavior as unknown.

## Phase 0–1 offline validation — 2026-11-16

Decoded facts reproduced from the raw snippets above (not new hardware observations):

- `fixtures/product.candump` lines 1–20 assemble to 134 bytes, confirming model
  `Siren 3 Pro`, software `1.0.0`, serial `21003942`, and the listed numeric
  metadata. Model version decodes as unavailable (empty/NUL with FF padding).
- `fixtures/pgn-lists.candump` lines 1–5 and 6–10 reproduce the advertised TX and
  RX lists above. Their CAN priorities differ (6 and 0); priority is not part of
  the reassembly identity. These lists are advertised capability only.
- `fixtures/discovery.candump` line 3 decodes as ISO NACK, source 5, destination
  35, referring to PGN 126998. It does not provide configuration contents.
- Line 6 retains the seven-byte heartbeat unchanged. CANboat's published layout
  and the reviewed simulator expect eight bytes. The decoder tags this as malformed;
  reason for the length difference remains unknown. Timestamp-free snippets cannot
  verify the claimed recurrence interval or fast-packet timing window.

Unknowns: full-session timestamps/directions, heartbeat length explanation,
configuration response contents, and behavior beyond the snippets. No inference
of application processing is made from TX/RX echo or silence. See fixture hashes
and provenance in `fixtures/README.md`; reproduction is documented in `README.md`.

Validation completed for phases 0–1: 56 automated tests passed with
`.venv/bin/python -m pytest -q`; `.venv/bin/ruff check .` and
`.venv/bin/ruff format --check src tests` passed. Installed `siren-mapper decode`
was exercised against the product fixture. Tests cover identifier bitfields,
ISO requests/NACK, both PGN lists, all discovery decoders, packet size limits,
loss/duplicates/order, timeouts, interleaving, raw-input preservation, and an
explicit socket-access guard. No live physical or virtual CAN tests were run;
no new device interaction is claimed.

## Phase 2 evidence reporting — 2026-11-16

No new hardware observations. Offline inventory and reports preserve the same
product evidence (`fixtures/product.candump` lines 1–20), advertised PGN lists
(`fixtures/pgn-lists.candump` lines 1–5 and 6–10), and malformed heartbeat
(`fixtures/discovery.candump` line 6) with exact file hashes and line provenance.
Observed PGNs are inventoried separately from PGNs advertised inside a list.
Source addresses remain unverified grouping keys; no persistent identity or
application processing is inferred.

Self-comparison of `fixtures/product.candump` returns `no_observed_change` under
the documented counts/payload comparison policy. The malformed discovery fixture
still returns capture-error exit status 1; comparisons containing its heartbeat
anomaly are `inconclusive`. These are tool behavior checks, not device findings.
Changed payload, timestamp, direction and conflicting-identity examples used in
tests are synthetic; they do not establish new Siren behavior.

Validation: 74 automated tests passed; Ruff lint and formatting checks passed.
Installed CLI smoke checks covered decode, inventory, self-diff, JSON report,
Markdown report and anomalous-input report. All three fixture SHA-256 hashes
remained unchanged. No physical CAN or vcan tests were run. Commands, schema,
confidence meanings and comparison limits are documented in
`docs/evidence-reports.md`; phase 3 and later remain pending.

## Phase 3 passive capture validation — 2026-11-16

Software validation only; no new physical Siren observations. Implemented explicit
`capture --channel ... --listen-only --output ...`, pre-open interface inspection,
finite duration/count, exclusive raw log plus metadata, SHA-256 provenance,
SIGINT/SIGTERM stop and receiver cleanup. Physical CAN is rejected unless already
UP at 250000 bit/s with kernel LISTEN-ONLY and user isolation/termination attestation.
No interface is reconfigured; mapper code has no transmit call.

Evidence: `tests/test_vcan_capture.py`, run with
`SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q tests/test_vcan_capture.py`.
The read-only interface inventory identified `vcan0` as an UP `vcan` link. All
three real SocketCAN subprocess cases passed: a separate test peer's synthetic
ISO request (source 219, requested PGN 126996) was recorded and decoded, the peer
observed no unexpected mapper traffic, and idle captures exited cleanly on SIGINT
and SIGTERM. These are virtual software tests, not Siren response evidence.

Default offline suite: 120 passed, three opt-in vcan cases skipped. Explicit vcan
suite: three passed. Ruff lint and formatting checks passed; all original fixture
hashes remained unchanged. Tests cover physical preflight rejection before socket
creation, exclusive-file handling, no-send behavior, timeout, startup/receive/
shutdown failures, signal cleanup, raw formatting, replay and final-file hashes.

Physical `can1` was not opened or configured. Wiring, termination, driver behavior,
actual electrical silence and real device traffic remain unverified. Phase 3
physical acceptance is pending the user-run procedure in `docs/passive-capture.md`.
No receive-drop measurement is implemented, and external interface reconfiguration
is not monitored after preflight; completeness and continued link settings remain
explicit limitations in capture metadata. Phase 4 transmission remains unimplemented.

## User-reported phase 3 bench follow-up

The user reported that the physical passive-capture procedure worked after
explicitly enabling kernel LISTEN-ONLY on the Raspberry Pi's `can1`. Their supplied
interface text before that change showed UP, ERROR-ACTIVE and 250000 bit/s, without
LISTEN-ONLY. No resulting raw capture, metadata or independent electrical-silence
measurement was supplied for review. Record this as user-reported operational
success, not independently verified Siren response/electrical acceptance evidence.

## Phase 4 active discovery validation — 2026-11-16

Implemented explicit bounded `discover --allow-transmit`, reusing the reviewed
`nmea2k-sim` address manager through an adapter rather than duplicating its claim/
conflict logic. It suppresses simulator engine telemetry, heartbeat and product/
PGN-list advertising. Every session records the simulator version and source hashes.
Physical preflight additionally requires hardware/isolation opt-in, 250000 bit/s,
normal classical CAN mode, and an assigned identity; unassigned manufacturer 2046
remains blocked on physical CAN, consistent with the simulator's existing policy.

The scheduler waits for source acquisition before directing one ISO request each
for 126996, 126998 and 126464. Candidate observations require matching source,
PGN/destination and an active request window; raw data and NACK/timeouts remain
separate from assertions about internal processing. Local-process candidates are
explicitly labeled. Duplicate NAME, address exhaustion, excessive claim traffic,
source conflicts, cancellation and duration limits are handled conservatively.

Evidence: `tests/test_discovery.py` replays the historical product/list/NACK fixtures
without opening CAN. `tests/test_vcan_discovery.py` uses a synthetic local peer named
`VIRTUAL DISCOVERY PEER`, not a physical Siren. Real vcan cases verified three bounded
queries, Product Information and both advertised lists, configuration NACK, source
relocation after a lower-NAME collision, acquisition/request spacing, raw evidence
replay, preserved-baseline comparison and pre-transmit cancellation. Captured TX
lines mean successful socket submission, not independently confirmed wire delivery.

Validation: `SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q` completed with
155 passed, including six real vcan cases (three passive, three active). Default
suite has 149 passing offline cases and six opt-in skips. Ruff lint and formatting
checks passed. No physical interface was opened or configured by this agent, no
new Siren responses were collected, and no sensor/configuration stimulus was sent.
Physical active discovery remains pending; reproduction, identity requirements,
baseline derivation and interpretation limits are in `docs/active-discovery.md`.

## Phase 4 unassigned bench-identity exception

User-provided CLI output showed physical discovery stopping at the manufacturer's
2046 guard before CAN startup. Added explicit `--allow-unassigned-bench-identity`
for this owned isolated-bench workflow. It is accepted only for code 2046 with
hardware/isolation/transmit opt-in and a verified physical CAN link. The default
physical rejection remains; all link, bitrate, claiming, collision, timing and
transmit-count gates remain in force. Session metadata records the exception and
labels the identity `unassigned_isolated_bench_exception`; it is not represented
as an assigned/certified identity. No simulator repository policy was modified.

Validation: 156 offline tests passed; six opt-in vcan cases skipped for this
follow-up. Ruff lint/format checks passed. New tests cover default rejection,
explicit acceptance, incomplete flag combinations, wrong manufacturer, vcan
rejection of the hardware exception, retained kernel-mode/bitrate checks, metadata
audit and CLI forwarding. No physical interface was opened and no hardware test
was run for this change. Updated Pi procedure is in `docs/active-discovery.md`.

## Phase 4a packet interpreter — 2026-11-17

Implemented the human-output contract from the Notion implementation plan's
November 17 addition (page `41acdfe6-c4fa-81c6-bace-e7d30d66e884`) and local phase
4a. Shared `PacketDecoder`/PGN registration/reassembly now serves structured
evidence, offline human replay and live display. `decode --human`, `replay`, and
human/verbose/raw options on existing capture/discovery commands introduce no
new CAN transmission behavior. Engine/fluid layouts are read-only additions;
no phase 5 sensor stimulus is implemented.

Original raw fixtures retain their recorded bytes and hashes. Product lines
1–20 produce exactly one completed Product Information summary, with 19 pending
fragment lines. Both advertised PGN-list roles and the configuration NACK are
explicitly named. The existing seven-byte heartbeat remains malformed; only
its known prefix is separately displayed as interval 60000 ms and sequence 225,
without padding or decoding missing tail fields. Structured evidence keeps those
prefix fields under `partial_decoded`, never as validated complete `decoded` data.
The interval is reported in the payload; recurrence still requires timestamps.

`fixtures/interpreter-synthetic.candump` contains 11 explicitly synthetic frames
for complete heartbeats/counters, 1200 RPM, signed dynamic engine values/status,
fluid level/capacity, and unknown/proprietary/transport examples. Its hash and
provenance are in `fixtures/README.md`. These are decoding vectors, not additional
Siren observations. Engine flags describe reported wire bits, not verified Siren
fault recognition. Unknown vendor bytes are never decoded as arbitrary text.

Validation: 192 tests passed with explicit `SIREN_MAPPER_VCAN=vcan0`, including
eight real vcan cases. The default suite passed 184 offline cases with eight
opt-in skips. New virtual receive-only cases compare live stdout exactly against
saved-file replay in both summary and verbose/raw modes and verify no mapper TX
at a separate peer. Offline tests cover all four formatting combinations,
scales/signedness/sentinels/enums/reserved bytes, fragmented/malformed/unknown data,
registry extension, timing/order, no offline sockets, and identical discovery TX
frames with/without human display. Ruff lint/format checks passed. No physical CAN
interface was opened or tested for this phase, and no new Siren interaction is
claimed. Commands and interpretation limits are in `docs/packet-interpreter.md`.

## Core device discovery and fingerprinting — 2026-11-17

Reviewed the updated Notion section “NMEA 2000 Device Discovery & Fingerprinting”
(page `41acdfe6-c4fa-81c6-bace-e7d30d66e884`). Implemented it as a reusable core
workflow: passive `devices`, passive-by-default live `discover`, offline
`discover --capture`, configurable mapper NAME fields, conditional directed NAME
acquisition, and exclusive versioned JSON/Markdown profile persistence. Existing
source claiming/conflict handling, physical mode/identity checks, bounded request
counts and human live interpretation remain in use. Sensor/wireless expansion
remains deferred.

The new `fixtures/siren-fingerprint.candump` is a curated regression ordering of
36 original November 16 CAN ID/data pairs: NAME claims, Product request/reply,
Configuration request/NACK, and PGN-list request/two role lists. It is not a newly
collected continuous session. No capture timestamps/directions were invented;
correlation is explicitly order-only/timing unknown. Original fixture files and
the seven-byte heartbeat anomaly remain unchanged. Synthetic unknown, malformed,
missing-configuration, collision and duplicate/unexpected fixtures are separately
labeled and hashed in `fixtures/README.md`.

Regression reproduces NAME `c08cab00815fffff`, unique number 2097151, manufacturer
code 1034, instance lower/upper 0/0, function 171, class 70, industry 4 and arbitrary
address capability 1 directly from NAME bitfields. It also reproduces product code
13939, database version 3100, model `Siren 3 Pro`, software `1.0.0`, serial 21003942,
certification field 2, LEN 1, and both advertised lists already recorded above.
These are decoded wire observations, not vendor lookup guesses or certification
of this mapper. The configuration NACK is explicitly “request not accepted,” with
control 1/raw bytes preserved, distinct from a completed timeout or an untimed
capture with no response observed.

Profiles retain request/source/destination/NAME references, raw hex, exact capture
timestamps, line numbers, source hashes, session limits/stop state and uncertainty.
A verified matching live sidecar reproduces its monotonic window qualification
without treating local echo as a remote response. Conflicting/duplicate/late or
wrong-source replies remain visible. NAME identity, current source/location,
source history and content changes are compared separately. Earlier source-only
unknown epochs are not retroactively assigned to a later NAME; unknown identities
are not matched across runs by address alone. Interrupted windows remain
inconclusive even when a complete payload was observed before interruption.

Validation: 222 tests passed with explicit `SIREN_MAPPER_VCAN=vcan0`, including
eight real vcan cases. Default suite: 214 passed, eight opt-in skips. Virtual peer
cases now verify target NAME acquisition before the three information queries,
Product and configuration NACK, separate TX/RX lists, source collision relocation,
JSON/Markdown output and exact equality of persisted live profiles with offline
reconstruction. CLI smoke checks saved/showed/diffed the recorded Siren fingerprint.
Ruff lint and format checks passed. No physical interface was opened/configured by
this agent; no new Siren interaction was collected. Physical end-to-end acceptance
requires user-run evidence and remains unverified. Procedure and scope limits are
in `docs/device-discovery.md` and the updated repository plan.
