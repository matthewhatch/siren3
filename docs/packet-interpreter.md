# Phase 4a: human-readable packet interpreter

Implemented from the repository's phase 4a and the
[Notion implementation plan, November 17 packet-interpreter addition](https://app.notion.com/p/41acdfe6c4fa81c6bacee7d30d66e884).
Notion was read for requirements; repository code and findings remain the implementation record.
No Notion task/status edits or new CAN transmission scenarios were added.

## Read an existing capture

```sh
.venv/bin/siren-mapper decode captures/discovery-002.candump --human
.venv/bin/siren-mapper replay captures/discovery-002.candump --verbose --raw
# Optional pacing at recorded speed (still entirely offline):
.venv/bin/siren-mapper replay captures/discovery-002.candump --speed 1
```

`decode` keeps its original JSON default. `--human` selects one line per frame;
`--verbose` selects multiple lines with all decoded fields, reserved fields and
capture line references. `--raw` includes each frame's CAN ID/payload and, on
completion, the assembled payload. Both `--verbose` and `--raw` imply human mode.
`replay` is human-readable by default. These commands only read files, require no
hardware dependency and never open a CAN interface.

`--speed 0` (default) replays without delays; positive finite speeds scale the
recorded timestamp gaps. Missing timestamps have no pacing delay, reversed gaps
are displayed in original order without a negative wait, and gaps are never used
to sort frames. Ctrl-C stops replay and reports pending assemblies. Exact input
timestamps remain visible; timestamp-free snippets display `timestamp=?` and
missing direction flags display `direction=?`, rather than inventing metadata.

## Live passive display on the Pi

After the existing physical setup has `can1` UP at 250000 bit/s with kernel
LISTEN-ONLY enabled on the isolated, verified bench:

```sh
mkdir -p captures
.venv/bin/siren-mapper capture \
  --channel can1 --listen-only --isolated-bench \
  --duration 75 --output captures/listen-human-001.candump \
  --human --verbose --raw
```

Choose unused output names; raw files and metadata retain exclusive-create
protection. The capture command remains strictly passive. Existing preflight,
finite duration/count, Ctrl-C/SIGTERM handling and evidence logs are unchanged.
See [passive capture](passive-capture.md) for link setup and limitations.

Human lines are flushed continuously to stdout. Logs and the final JSON session
summary go to stderr so they do not mix with packet text. The raw `.candump` file
and `.json` sidecar remain the durable evidence. Decode errors print diagnostics
and do not terminate capture. Human-mode commands exit 1 for malformed, incomplete
or timed-out decoding, even if raw capture itself completed normally; operational
capture/file errors exit 2. The known seven-byte heartbeat can therefore produce
exit 1 with a fully preserved raw capture.

The same flags can be appended to an **already explicitly authorized** `discover`
command to display its existing TX submissions and received traffic. They do not
start discovery, alter PGNs, add requests, bypass identity/interface gates, or
change transmission budgets. The formatter receives each recorded raw line after
it has been flushed to the evidence file. Console work can add latency; it is
not a real-time timing instrument. [Discovery setup](active-discovery.md) remains
separate from passive capture.

## What each line means

Every valid NMEA CAN frame displays its exact timestamp, interface, direction,
priority, source/destination, PGN/name, payload length and interpretation. PDU2 or
global-addressed messages use `dst=ALL`. `TX/local (origin unverified)` retains
SocketCAN/local-TX ambiguity; `RX (origin unverified)` never establishes that the
Siren independently replied or processed a request. Discovery T lines still mean
socket submission, not measured wire delivery. Unsupported non-NMEA input produces
a per-line diagnostic; `--raw` reveals its original line without guessed fields.

For example, the synthetic rapid-update vector produces fields including:

```text
... can1 TX/local (origin unverified) priority=2 src=35 dst=ALL PGN=127488 Engine Parameters, Rapid Update len=8 | Engine parameters reported; device processing is unverified | Engine instance=0; Engine speed=1200 RPM; ...; Tilt/trim=-4 %
```

This example is a synthetic decoding demonstration, not a new Siren observation.

Fast-packet fragments display sequence/frame indexes and `pending`, with no
invented message fields. The final frame displays **one** completed-message
summary with frame/byte counts and decoded fields. The original twenty-frame
product fixture yields model `Siren 3 Pro`, software `1.0.0`, serial `21003942`.
PGN lists explicitly say **advertised transmit** or **advertised receive**;
configuration NACKs explicitly name PGN 126998. A list is advertised capability,
not evidence that the target consumes every advertised PGN.

The original seven-byte heartbeat remains malformed against the eight-byte
layout. Its known three-byte prefix is displayed separately as interval 60000 ms
(60 s), sequence 225, labeled **known prefix only**. Missing tail fields are not
padded or decoded. In structured evidence its status remains `malformed`, with
`partial_decoded` separated from validated `decoded` fields. Eight-byte synthetic
vectors exercise consecutive counters 224/225. An interval carried in a payload
is a reported value; actual recurrence still requires capture timestamps.

## Supported fields and unknown data

Existing discovery decoding is retained. Registry entries now include:

| PGN | Presentation/decoding |
| --- | --- |
| 59392, 59904, 60928 | ISO acknowledgement, request, NAME/address claim |
| 126464, 126993, 126996, 126998 | Advertised lists, heartbeat, product, configuration |
| 127488 | Engine instance, quarter-RPM speed, boost pressure in Pa, signed trim % |
| 127489 | 26-byte fast packet: pressures in Pa, temperatures in K, signed voltage in V and fuel rate in L/h, running time in s, reported status bits, signed load/torque % |
| 127505 | Tank instance/type, signed level at 0.004%, capacity in L |
| 60160, 60416 | Named ISO transport PGNs, payload not decoded; no ISO-TP assembly |
| 126208 | Named group function, fast-packet assembly only; fields not decoded |

Unsigned/signed endian order, scales and numeric sentinel ranges are tested.
Unavailable/error/reserved numeric sentinel values are not displayed as real
measurements. Raw bytes remain available for exact sentinel inspection; nulls are
shown as `unavailable/invalid`. Reserved bytes/bits are retained for verbose/hex
inspection. Controller states, equipment status, tank types and known engine
status bits have labels; unrecognized enum values and status bits retain their
numeric/hex representation. Reported status bits are not an inferred Siren alarm.
Fluid levels are not clamped to 0–100%; representable signed values are preserved.

Unknown and proprietary payloads are explicitly labeled and never interpreted as
text simply because their bytes are printable. No speculative vendor decoder is
installed. Standard transport names do not imply payload decoding support.

`PacketDecoder` is the shared incremental pipeline used by structured evidence
and both human replay and live callbacks. It reuses `Reassembler` and the
`protocol.PGNS` registry. `register_pgn(pgn, PgnDefinition(...))` registers a name,
read-only decoder and fast-packet policy for all consumers. New definitions need
source-backed layout/sentinel tests; no encoder or transmission code is required.
The small dependency-free sensor readers were added after reviewing the existing
simulator's rapid/dynamic codecs and CANboat layouts. Offline functionality does
not require importing or running the simulator.

Ordering, duplicates, truncation, assembly windows and errors use the shared
transport policy. Timeouts are checked against capture timestamps as frames
arrive; an idle incomplete stream is reported on stop/EOF, not by inventing later
capture timestamps. The human renderer escapes control characters in channel
and decoded text fields; it does not execute or display payloads as terminal control codes.

## Reproduce validation

```sh
.venv/bin/siren-mapper decode fixtures/product.candump --human
.venv/bin/siren-mapper replay fixtures/pgn-lists.candump --verbose --raw
.venv/bin/siren-mapper replay fixtures/discovery.candump
.venv/bin/siren-mapper replay fixtures/interpreter-synthetic.candump --verbose
.venv/bin/python -m pytest -q
SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests
```

The discovery fixture intentionally exits 1 for its original heartbeat anomaly.
The new synthetic fixture is separately identified in `fixtures/README.md`;
original capture bytes were not changed. Mocked live/replay tests cover all four
verbose/raw combinations using synthetic timestamps/directions over the historical
product/list/NACK payloads, and confirm continued output after malformed data.
Real vcan cases compare stdout **exactly** with replay of the saved log in summary
and verbose/raw modes, reconstruct the historical product, display 1200 RPM and
a synthetic heartbeat, and check for no mapper transmission at an independent peer.
A separate test verifies that discovery's actual TX identifiers/payloads/counts
are identical with and without human display. No physical CAN testing was run.

Sources: the linked Notion addition, existing immutable fixture inventory, reviewed
`nmea_sim/pgn_127488.py` and `pgn_127489.py`, and
[CANboat's PGN definitions](https://canboat.github.io/canboat/canboat.html)
for layouts, scales and enum labels. Reverse-engineered definitions and software
validation do not establish NMEA certification or new device interaction.
