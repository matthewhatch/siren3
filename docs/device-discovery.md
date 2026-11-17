# Core NMEA 2000 device discovery and fingerprinting

This implements the expanded November 17
[Notion Device Discovery & Fingerprinting workflow](https://app.notion.com/p/41acdfe6c4fa81c6bacee7d30d66e884).
Discovery is a reusable application workflow, not a Siren-specific decoder demo.
Sensor scenarios and wireless expansion remain deferred. Physical active discovery
has not been run by this agent; static regression and real vcan validation are complete.

## Offline: reproduce the recorded Siren fingerprint

```sh
# No CAN sockets, simulator or hardware dependencies:
.venv/bin/siren-mapper discover \
  --capture fixtures/siren-fingerprint.candump --target 5 \
  --profile-output /tmp/siren-fingerprint-001.json --format human
.venv/bin/siren-mapper profile show /tmp/siren-fingerprint-001.json
.venv/bin/siren-mapper profile show /tmp/siren-fingerprint-001.json --format markdown
```

The fixture is a **curated sequence of original November 16 CAN ID/data pairs**,
not a newly collected continuous session. Product/list bytes and claims/NACK
are unchanged; timestamps and directions were not invented. See the fixture
inventory for provenance and hashes. Order-only correlation remains explicitly
`capture_order_only_timing_unknown`.

Expected selected-device fingerprint:

| Field | Decoded observation |
| --- | --- |
| ISO NAME | `c08cab00815fffff` |
| Unique number / manufacturer code | `2097151` / `1034`, decoded from NAME bits |
| Instance lower / upper | `0` / `0` |
| Function / class / industry | `171` / `70` / `4` |
| Arbitrary-address capability | `1` |
| Product code / NMEA database version | `13939` / `3100` |
| Model / software / serial | `Siren 3 Pro` / `1.0.0` / `21003942` |
| Model version | unavailable; not filled with a guessed string |
| Certification / load equivalency | `2` / `1 LEN` |
| Configuration | **request not accepted**, negative ACK control `1`, raw `01FFFFFFFF16F001` |
| Advertised TX | 59392, 59904, 60160, 60416, 60928, 126208, 126464, 126993, 126996 |
| Advertised RX | 59392, 59904, 60160, 60416, 60928, 65240, 126208, 127488, 127489, 127505 |

No manufacturer is inferred from the model name, and source address 5 is not a
permanent identity. Product Information is assembled from all twenty recorded
frames; list direction comes from each assembled payload's function byte, never
its CAN priority. Priorities 6 (TX list) and 0 (RX list) remain separate evidence.
132-byte standard Product payloads lacking optional certification/load bytes are
also supported, with those fields unavailable; malformed 133-byte payloads fail.

For your existing completed capture:

```sh
.venv/bin/siren-mapper discover --capture captures/discovery-002.candump --target 5 --format human
```

Offline discovery always saves JSON and Markdown snapshots. Without
`--profile-output`, the first is `<capture>.profile.json`; subsequent offline
runs choose unused `<capture>.profile-002.json`, `-003.json`, etc. Markdown is
`<profile-json>.md`. Reports for identical bytes/options are deterministic; export
filename increments do not change profile content. An explicit occupied profile
path fails instead of overwriting. The input capture is read only.

If an active `<capture>.json` sidecar exists, its raw-file SHA-256 must match.
Recorded candidate line references and monotonic request windows then reproduce
live qualification, including interrupted windows. With no sidecar, correlation
uses source, requested PGN, destination, capture order and available timestamps.
It never manufactures elapsed time for old snippets. Do not replay a session until
its raw log/sidecar have finished writing.

## Passive device inventory on the Pi

With the selected interface already UP at 250000 bit/s, kernel LISTEN-ONLY enabled,
and the isolated bench/termination checked:

```sh
mkdir -p captures
.venv/bin/siren-mapper devices --interface can1 --isolated-bench \
  --duration 75 --output captures/devices-001.candump
```

This opens only the existing receive-only capture adapter. It displays observed
source addresses/NAME confidence, PGNs seen during the bounded capture, last
capture timestamp, and any product/configuration/list facts already on the bus.
A quiet or incomplete inventory is not proof of absence. Passive inventory never
asks a device to transmit its NAME or product data. Existing kernel listen-only,
bitrate and isolation checks remain in force. `--count N` optionally bounds frames.

`--channel` and `--interface` are aliases. Add `--human --verbose --raw` to see every
packet through the existing interpreter as it arrives. Its stdout matches file
replay; the final fingerprint summary goes to stderr when packet display is active.
Without packet display, the device summary is printed to stdout at capture end.
`--format json|markdown|human` chooses a profile report format. `devices --capture
FILE` provides the same passive inventory from an existing file, entirely offline.

`discover --interface ... --output ...` is also **passive by default** when no
`--allow-tx`/`--allow-transmit` is present. Hardware/identity transmit options without
that permission are rejected; simply choosing an interface never causes a request.
Live modes require `--output` so raw evidence is always retained. Offline mode
uses `--profile-output` for exports, not the live raw-log `--output` option.

## Explicit targeted live discovery

Select the current source address from observed traffic. Use a distinct mapper
NAME, and stop another simulator using that same identity. Install the reviewed
simulator dependency as described in [active discovery](active-discovery.md).
On the verified isolated bench, externally configure normal classical CAN at
250000 bit/s (kernel listen-only off) before active use; the mapper never changes
link settings. The passive adapter and active adapter have different mode gates.

For the user's existing unassigned-2046 bench setup:

```sh
.venv/bin/siren-mapper discover \
  --interface can1 --target 5 --source-address 35 \
  --hardware --isolated-bench --allow-tx \
  --manufacturer-code 2046 --allow-unassigned-bench-identity \
  --unique-number "$DISTINCT_UNIQUE_NUMBER" \
  --output captures/discovery-003.candump --human
```

The unique-number variable must be a distinct integer in 1–2097151, not the full
hexadecimal NAME. An assigned identity can be used without the 2046 exception.
The exception remains explicit, isolated-bench-only and audited. No code is assigned
or certified merely because the mapper accepts a bench identity.

Mapper identity fields are configurable: `--device-instance` (0–255, combined
lower/upper instance bits), `--device-function` (0–255), `--device-class` (0–127),
`--system-instance` (0–15), and `--industry-group` (0–7). Defaults retain the
reviewed simulator profile (instance 0, function 140, class 50, system 0, marine
industry 4). Arbitrary-address capability stays enabled for collision handling.
Do not clone the target's NAME or invent manufacturer assignments.

The automated sequence is:

1. Record a baseline and learn observed source occupancy and target NAME. No
   mapper data frames are submitted during this period; a normal-mode physical
   controller may still acknowledge incoming CAN frames.
2. Use the simulator address manager to claim an unoccupied mapper source,
   reserve the target address, and wait >=250 ms after claim submission. Detect
   duplicate NAMEs, relocate after losing conflicts, and pause after reassertion.
3. If the baseline did not reveal the target NAME, send a **directed ISO request
   for 60928**, payload `00EE00`. An observed NAME is preferred over guessing an
   identity from heartbeat/model/source. A timeout leaves identity unknown.
4. Send targeted ISO requests for Product (126996, `14F001`), Configuration
   (126998, `16F001`) and PGN List (126464, `00EE01`). Reassemble full payloads;
   collect both transmit and receive list functions during the list window.
5. Record recovery and finalize the raw log, session metadata, versioned device
   profile and Markdown report. Print per-query progress and fingerprint results.

This replaces manual `cansend` operations; users never have to assert source
ownership manually. All existing preflight/transmit flags remain required. No
configuration writes, group-function fallback, sensor simulation or wireless
operations are added. Default retries are zero (one attempt per requested PGN);
retry explicitly as a new, separately preserved discovery run if appropriate.

At most four ISO requests are sent (three if NAME was already observed), with
>=1 second between requests and a default 2-second full response window. At most
20 claim frames, four claims in a rolling second, and 24 total TX frames are
allowed. Overall duration defaults to 30 seconds, capped at 60; duration/signal
stop may leave the profile incomplete. Existing baseline/window/recovery controls
remain available. Ctrl-C/SIGTERM stop promptly and preserve partial evidence.
A target NAME replacement or movement stops active discovery rather than silently
querying a different node; inspect the profile and choose the new source explicitly.

Live artifact names:

- `<output>`: original candump-style submissions/observations, never overwritten.
- `<output>.json`: session limits, request outcomes, conflicts, stop reason and raw hash.
- `<output>.profile.json`: schema-version-1 device fingerprint bundle.
- `<output>.profile.json.md`: readable profile plus complete structured evidence.

`--profile-output PATH` selects a different profile filename. Artifact paths are
reserved exclusively before the active socket opens; a collision fails before
any claim/request. Errors and interrupted runs still preserve whatever profile
can be derived from captured evidence. As with raw capture, disk/power/SIGKILL
failures cannot guarantee complete finalization. In active JSON mode stdout is
session metadata containing profile paths; `--format human|markdown` prints the
profile instead. `--human` streams packets to stdout and prints progress/fingerprint
summary to stderr. The same decoder/registry powers offline and live packet text.

## Profile meaning and comparison

```sh
.venv/bin/siren-mapper profile show captures/discovery-003.candump.profile.json
.venv/bin/siren-mapper profile show captures/discovery-003.candump.profile.json --format json
.venv/bin/siren-mapper profile diff captures/discovery-002.candump.profile.json captures/discovery-003.candump.profile.json
```

The profile stores:

- `identity`: raw NAME, verified-layout NAME fields, and confidence explicitly
  limited to an observed claim. No independent sender authentication is implied.
- `locations`, `current_location`, first/last capture timestamps, observed PGNs,
  and frame count. Source-only unknown epochs are kept separate from later NAME
  claims; earlier traffic is not retroactively assigned an identity without evidence.
  Multiple entries at one source can describe different observed identity epochs,
  not necessarily different physical devices.
- Product fields, configuration observations, separate advertised TX/RX lists,
  requests and response provenance: original frames, timestamps, CAN IDs, raw hex,
  line numbers, source-file SHA-256 and request references.
- Correlation basis: known target NAME at request time when available; response NAME,
  source/PGN/destination/time window; duplicate and unexpected-reply diagnostics.
- A concise hash-linked session context when available: mode, stop reason,
  duration/request limits, mapper identity and the audited physical-identity exception.
- Errors, conflicting alternatives, partial data and unresolved questions.

A configuration NACK is `request_not_accepted`, retaining control `1` and raw
acknowledgement bytes. A measured completed window with no qualifying reply is
`timeout`. An untimed/short capture with no reply is `no_response_observed`.
An ACK directed elsewhere is `uncorrelated_acknowledgement_observed`, not rejection
of our request. Conflicting replies are retained rather than using the last one.
Interrupted windows are `inconclusive_request_window` even when some fields were
already observed. Unknown/proprietary payloads remain unknown/raw.

A request destination alone never creates a fictitious detected device. Standard
transport support remains the existing fast-packet decoder; ISO-TP payloads are
retained but not automatically interpreted. NAME claims, local loopback, socket
send success and bus ACKs cannot prove internal Siren processing. Advertised PGNs
do not establish alerts, health, measurement use, cloud or wireless capability.

`profile diff` matches observed NAME claims, reports current address changes and
history changes separately from product/configuration/advertised-PGN changes,
and ignores evidence timestamps/file paths when comparing contents. Unknown
source-only identities remain unmatched across runs, even if their addresses agree.
Matching NAMEs is useful evidence, not proof that two physical nodes are identical.

## Validation and provenance

Static cases in `tests/test_profiles.py` reproduce the recorded Siren fingerprint,
all NAME bits, NACK distinction, separate lists/priorities, identity epochs,
mutable addresses, duplicate/wrong-source replies, missing configuration,
malformed packets, profile persistence/diff, no offline sockets and passive no-TX.
`tests/test_discovery.py` verifies source acquisition, conditional NAME requests,
configurable identity, conflict/duplicate handling, budgets and pre-TX artifact
reservation. Real vcan uses a synthetic `VIRTUAL DISCOVERY PEER`, not a physical
Siren, and verifies NAME acquisition, product/configuration/list outcomes,
JSON/Markdown persistence, relocation, replay and baseline comparison. Existing
human-output consistency/no-TX cases remain enabled.

```sh
.venv/bin/python -m pytest -q
SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests
```

Physical `can1` acceptance is a user-run isolated-bench step. Do not mark it complete
without the actual raw capture/sidecar/profile and measured bench setup. No new
physical observations were collected in implementing this workflow.
