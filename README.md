# Siren 3 Mapper

Python 3.11+ CLI for offline characterization of an owned Siren 3 Pro.
Phases 0–2 and phases 3–4 software are implemented. Running without a command
prints usage. Offline commands require local files and never open a CAN socket.
`capture` is strictly passive; `discover` requires explicit transmit opt-in and
simulator-managed address claiming. No command configures an interface. Physical
passive capture was reported successful by the user; physical discovery is untested.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/siren-mapper decode fixtures/product.candump
.venv/bin/siren-mapper decode fixtures/pgn-lists.candump
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Without installation: `PYTHONPATH=src python -m siren_mapper decode fixtures/product.candump`.
The decode command emits deterministic JSON with original lines,
line numbers, exact timestamp strings, direction flags and file SHA-256.
Exit status is 0 for valid input, 1 for capture/packet errors, 2 for file errors.
Unknown PGNs retain payload bytes without guessed decoding. Errors never emit
partially reconstructed protocol fields. Raw files are read only.

Supported input: eight-digit extended IDs in compact candump format, optional
`(seconds.fraction)` timestamps, or verbose `[DLC]` hex bytes; optional standalone
`T`/`R` flags before or after the ID/payload. Empty/comment lines are ignored.
Standard IDs, CAN FD, RTR, error frames and other formats are explicitly rejected.
Direction flags are metadata and do not establish remote device processing.

Fast packets are strictly ordered and isolated by channel, source, PGN,
destination and three-bit sequence. Payloads are 1–223 bytes; capacity is 64
incomplete packets. The absolute timestamp window is 0.75 seconds; reversed
timestamps, duplicates, loss and truncation reject a packet. A fresh start is
required after rejection. Without capture timestamps only ordering and length
can be checked; elapsed time is unknown. Incomplete packets at EOF are errors.
Only known fast-packet PGNs (126464, 126996, 126998) are assembled.

The product fixture reconstructs 20 frames into 134 bytes: model `Siren 3 Pro`,
software `1.0.0`, serial `21003942`. Both advertised PGN lists reconstruct.
`fixtures/discovery.candump` intentionally exits 1 because its observed seven-byte
heartbeat differs from the expected eight-byte layout. Its original payload is
retained with a length error. This is an evidence anomaly, not a claim about
why the device emitted that length. Configuration decoding has synthetic tests;
the actual snippet contains only a NACK for configuration PGN 126998.

See [phase 0 inventory](docs/phase-0-inventory.md),
[fixture provenance](fixtures/README.md) and [findings](docs/known-findings.md).
[Evidence report reference](docs/evidence-reports.md) describes the schema, confidence
labels and comparison policy. [Passive capture](docs/passive-capture.md) describes
phase 3 gates and its physical smoke-test procedure. [Active discovery](docs/active-discovery.md)
describes phase 4 setup and evidence. Real vcan capture, discovery and conflict
handling have been tested. The user reported passive `can1` capture worked; its
raw capture has not been reviewed here. No physical discovery has been run by this agent.

## Inventory, compare and report

```sh
.venv/bin/siren-mapper inventory fixtures/pgn-lists.candump
.venv/bin/siren-mapper diff fixtures/product.candump fixtures/product.candump
.venv/bin/siren-mapper report fixtures/product.candump --format json
.venv/bin/siren-mapper report fixtures/product.candump --format markdown
```

All commands write to stdout. To save a report, redirect stdout to a new path
outside your raw capture collection. JSON is the default report format.
Inventory separates observed PGNs from advertised capability and retains every
identity/list observation with evidence references. It groups by channel and
source address; address ownership and continuity remain unverified.

Diff compares counts and complete payload frequencies per
channel/source/destination/PGN. It excludes timestamps, direction, priority,
fast-packet sequence and message order. It reports `observed_change`,
`no_observed_change`, or `inconclusive` when either input has errors. These labels
are comparisons of captures, not conclusions about stimulus causality or device
application processing. Collection duration and setup must be assessed separately;
counts are not normalized to rates. Differences alone do not cause a nonzero exit.

## Explicit passive capture

```sh
.venv/bin/python -m pip install -e '.[hardware]'
# Requires an existing UP interface verified as virtual CAN:
.venv/bin/siren-mapper capture --channel vcan0 --listen-only --duration 10 --output /tmp/siren-vcan-001.candump
```

There is no default channel. `--listen-only` is required, duration defaults to
60 seconds, and Ctrl-C or SIGTERM stops capture and closes the receiver. Raw output
and a `.json` metadata sidecar use exclusive creation and never overwrite existing
files. Physical CAN additionally requires an already UP, 250 kbit/s, kernel
LISTEN-ONLY interface and `--isolated-bench` to attest isolation and verified
termination. Read the [bench smoke-test procedure](docs/passive-capture.md) first.

## Bounded active discovery

Install the reviewed simulator checkout as a separate package; it supplies address
management without copying its implementation:

```sh
.venv/bin/python -m pip install -e '.[hardware]'
.venv/bin/python -m pip install -e ../nmea2k-sim
# Explicit virtual test; a responder must be running separately to obtain replies:
.venv/bin/siren-mapper discover --channel vcan0 --target 5 --unique-number 123456 --allow-transmit --output /tmp/siren-discovery-001.candump
```

The command claims an available source, then requests PGNs 126996, 126998 and
126464 once each, with bounded observation windows and capture of all traffic.
The `.json` sidecar records outcomes, request/response line references, simulator
module hashes, conflicts, limits and unresolved questions. NACKs and timeouts
remain observations rather than inferred capability. The combined raw log replays
through existing inventory/report/diff commands.

Physical discovery also requires `--hardware --isolated-bench`, normal CAN mode
with kernel LISTEN-ONLY disabled externally, 250 kbit/s, verified termination and
a legitimately assigned manufacturer identity. The simulator's default unassigned
2046 identity is blocked on physical CAN. See [active discovery](docs/active-discovery.md)
for the complete procedure and how to compare a preserved baseline.
