# Phase 4: bounded active discovery

Software is implemented and tested on virtual CAN. Physical discovery on the
Raspberry Pi/`can1` has **not** been run by this agent. The user's successful passive
capture report does not establish active discovery behavior.

## Dependency and identity

The mapper imports the existing simulator as an adapter, without starting its
engine process or copying its source-management implementation:

```sh
.venv/bin/python -m pip install -e '.[hardware]'
.venv/bin/python -m pip install -e ../nmea2k-sim
```

Adjust the sibling checkout path for your Pi. The reviewed simulator distribution
is `nmea-engine-sim` 0.1.0, with `nmea_sim.device.Device`, `device_protocol.Name`
and `can_id.compose`. Each session records its package version and source-file
SHA-256 hashes. Keep both checkouts at reviewed versions; an incompatible adapter
must be fixed/tested before hardware use. Offline mapper commands do not import
or require this package.

`--unique-number` is required and must be distinct from every other node using
the same manufacturer/NAME profile on the bench (1–2097151). It is an explicit
identity choice, not a copied Siren serial or automatically reused simulator NAME.
The adapter inherits the simulator NAME's class 50/function 140 engine profile;
it does not send engine telemetry or advertise simulator Product/PGN lists.
This identity profile must be considered when interpreting a black-box experiment.

Default manufacturer 2046 is the simulator's **unassigned** test identity. By
default it is rejected on physical CAN. The mapper now permits a narrowly scoped
exception with `--allow-unassigned-bench-identity`, only when all of
`--hardware --isolated-bench --allow-transmit --manufacturer-code 2046` are selected.
This permits the existing test identity on the owned isolated bench; it does not
make that identity assigned or certified. The simulator package's own physical-TX
policy is unchanged. Without the explicit exception, physical discovery requires
an operator-supplied assigned code within 1–2044. Never substitute an arbitrary
manufacturer's number or clone the Siren's NAME. The exception is recorded in the
session metadata and does not bypass bitrate, mode, claiming, collision or rate checks.

## Virtual use

```sh
# Requires an already UP vcan0, and a separately managed target peer at address 5:
.venv/bin/siren-mapper discover --channel vcan0 --target 5 --unique-number 123456 --allow-transmit --output /tmp/discovery-001.candump
.venv/bin/siren-mapper inventory /tmp/discovery-001.candump
.venv/bin/siren-mapper report /tmp/discovery-001.candump --format markdown
```

With no responder, the requests time out; this is an observation, not an
unsupported-PGN conclusion. The built-in integration test creates its own
explicitly synthetic virtual peer, so no physical Siren is required for validation.

There is no default interface or target. Broadcast/null targets and reserved
source addresses are rejected. The preferred source defaults to 35, but an
occupied source causes selection of another address. Do not run another node
with the same ISO NAME. Source address alone does not define identity.

## Physical Pi bench procedure (user-run, pending)

Use only the owned, isolated, correctly terminated 250 kbit/s bench, separated
from all operational vessel networks. Confirm wiring, termination measurement,
power, adapter mode and distinct identity before proceeding. Use an assigned
identity or explicitly select the unassigned-2046 isolated-bench exception. These are
physical operator checks; `--isolated-bench` records your attestation.

The passive phase left `can1` in kernel LISTEN-ONLY. Active discovery requires
that mode to be disabled externally. Changing it interrupts other users of the
interface. Do not run the passive mapper command while the link is in active mode.
On the verified isolated bench only, deliberately configure the adapter:

```sh
sudo ip link set dev can1 down
sudo ip link set dev can1 type can bitrate 250000 listen-only off
sudo ip link set dev can1 up
ip -json -details link show dev can1
```

Use real operator-supplied identity values below; the shell placeholders are
intentionally not prefilled. The command refuses the default 2046 on physical CAN
unless the explicit bench exception is selected.

```sh
mkdir -p captures
.venv/bin/siren-mapper discover \
  --channel can1 --hardware --isolated-bench --allow-transmit \
  --target 5 --source-address 35 \
  --manufacturer-code "$ASSIGNED_MANUFACTURER_CODE" \
  --unique-number "$DISTINCT_UNIQUE_NUMBER" \
  --output captures/discovery-001.candump
```

For your existing unassigned-2046 bench setup, keep the simulator process stopped
for this discovery run and use a distinct unique number:

```sh
.venv/bin/siren-mapper discover \
  --channel can1 --hardware --isolated-bench --allow-transmit \
  --manufacturer-code 2046 --allow-unassigned-bench-identity \
  --unique-number "$DISTINCT_UNIQUE_NUMBER" \
  --target 5 --source-address 35 \
  --output captures/discovery-002.candump
```

Use an unused output name. Do not reuse a running simulator's full NAME; duplicate
NAME detection will stop discovery even when the source addresses differ. The
exception flag is rejected without explicit hardware/isolation mode or with a
manufacturer code other than 2046. It does not affect passive capture.

The command never configures interfaces. Preflight requires an exact UP physical
CAN link at 250000 bit/s, a usable controller state, no LISTEN-ONLY/LOOPBACK/FD/
PRESUME-ACK, explicit hardware/transmit flags and isolation attestation. It rejects
ambiguous metadata before opening a socket. A physical link renamed `vcan0` still
requires the physical gates. Interface settings are checked once; keep them under
exclusive administrative control so another process cannot change them mid-run.

Stop immediately with Ctrl-C or SIGTERM if needed. Once finished, externally
restore kernel listen-only before resuming passive capture:

```sh
sudo ip link set dev can1 down
sudo ip link set dev can1 type can bitrate 250000 listen-only on
sudo ip link set dev can1 up
```

Keep raw logs and sidecars. Record topology, adapter/driver, measured termination,
identity allocation, link metadata, hashes, stop reason and source/PGN/timestamp
line references in the findings ledger. Do not infer success from a transmit echo,
CAN acknowledgement or socket send return value. No destructive commands,
configuration writes, group functions, pairing or sensor/alarm injection are sent.

## Schedule, bounds and cancellation

1. Receive a baseline (default 1 second), recording traffic without transmitting.
   Learn occupied sources; reserve the target address so the mapper cannot claim it.
2. Use the existing simulator's NAME comparison and address-conflict state machine
   to claim an available source. Wait at least 250 ms after socket acceptance of a
   claim before ISO requests. Winning conflicts reassert and restart the wait;
   losing conflicts relocate and pause requests. Duplicate NAME or address
   exhaustion stops discovery. A request interrupted by loss/reassertion of its
   source becomes `inconclusive_address_change`; it is not retried.
3. Send exactly one directed ISO Request per PGN, in order: Product Information
   126996, Configuration 126998, PGN List 126464. Listen for a full response window
   (default 2 seconds) for each; start requests at least 1 second apart. Both list
   functions must be observed for the PGN-list outcome to be complete.
4. Receive a recovery period (default 1 second) with no further discovery requests.
   Address-management replies may still occur to maintain safe ownership.

Limits: three discovery requests, at most 20 claim frames, at most four claims in
any rolling second, and at most 24 total frames. A claim-rate/budget violation
stops instead of queuing unbounded work. The overall duration defaults to 30 seconds
and is capped at 60. Duration expiry may leave some requests unsent; examine the
metadata rather than assuming all three ran. `--baseline`, `--response-window`
and `--recovery` accept 0.2–10 seconds; `--interval` accepts 1–10; `--duration`
accepts 1–60. All values must be finite. Requests are never automatically retried.

Traffic is drained in bounded batches before new ownership-dependent requests.
Persistent receive backlog postpones requests rather than sending while conflicts
may remain queued. Cancellation is checked before sends; receive waits are 20 ms
and send timeouts 100 ms. SIGINT/SIGTERM flush, close and finalize metadata; SIGKILL
or power/disk failure cannot guarantee finalization. Normal completion, timeouts,
NACKs and clean signals exit 0; validation/socket/I/O/budget failures exit 2.

## Evidence and interpretation

The raw candump-style file and `<output>.json` use exclusive creation, preserving
existing evidence. A partial/error session retains frames, attempted-send events,
status and the actual raw-file hash. A send is logged as T only after the socket
accepts it; its timestamp is the submission time, **not a measured wire delivery**.
The socket suppresses its own receive echo; other local senders remain visible
and participate in conflict handling. Local loopback is enabled so independent
observers can see mapper submissions, unlike the strictly passive capture socket.

The sidecar records per-request source/target, request line, monotonic send/window
schedule, observations and status. An observation qualifies as a candidate only
when its source matches the target, PGN matches the outstanding query (or a directed
ISO ACK referencing it), destination is compatible, and it arrives in that request's
window. Fast packets must fully reassemble and decode. A target NAME change stops
discovery. This is temporal correlation, not proof of remote origin or causality.
Local-process candidates have `origin_hint: local_origin`; other candidates remain
`bus_receive_unverified`. T/R metadata never overrides source/PGN/window checks.

Outcomes:

| Status | Meaning |
| --- | --- |
| response_observed | Complete candidate payload; PGN list includes both TX/RX functions. |
| acknowledgement_observed | ACK/NACK/access-denied/busy observed; inspect `observations[].kind`. |
| timeout | No qualifying complete candidate before the deadline; capability remains unknown. |
| partial_pgn_lists | Only one advertised-list function observed. |
| inconclusive_conflicting_observations | Both content and negative ACK, or conflicting ACK controls. |
| inconclusive_address_change | Source conflict invalidated the request window. |
| inconclusive_stopped | Stop/error/duration occurred before the window finished. |

Malformed, missing, unrelated and late frames remain in the raw capture, even if
they cannot satisfy a request. Decode/assembly errors are also noted in session
events. No receive-drop count is collected; completeness and silent device
processing remain unknown. Captured PGN lists are advertised capability only.
Historical fixture NACK for 126998 is a regression observation, not a guarantee
that every future configuration query will NACK.

## Reproduce baseline comparison

You can compare an independently preserved passive baseline with the combined
session using `siren-mapper diff baseline.candump discovery-001.candump`. Counts
will reflect different durations and the mapper's own new claim/request traffic;
the comparison cannot establish stimulus causality.

For an exact pre-transmit segment of a discovery run, the `baseline_complete`
event records `final_line`. This offline example verifies the parent hash and
creates a new derived baseline without modifying the original capture:

```sh
.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path
raw = Path('captures/discovery-001.candump')
meta = json.loads(Path(str(raw) + '.json').read_text())
data = raw.read_bytes()
assert hashlib.sha256(data).hexdigest() == meta['sha256']
end = next(e['final_line'] for e in meta['events'] if e['kind'] == 'baseline_complete')
baseline = Path('captures/discovery-001.baseline.candump')
with baseline.open('xb') as output:
    output.write(b''.join(data.splitlines(keepends=True)[:end]))
print('Derived from', meta['sha256'], 'through line', end)
PY
.venv/bin/siren-mapper diff captures/discovery-001.baseline.candump captures/discovery-001.candump
```

Keep the original sidecar with that derived artifact to preserve provenance.
The original capture also retains baseline, request and recovery traffic; it is
not a clean stimulus-only recording. Empty or short baselines and changing
collection conditions limit interpretation.

## Validation

```sh
.venv/bin/python -m pytest -q
SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests
```

Default tests open no CAN sockets. Opt-in vcan tests reject physical links before
opening or sending. A separate virtual peer, labeled `VIRTUAL DISCOVERY PEER`,
returns synthetic Product Information, a configuration NACK and two PGN lists.
Integration cases verify all three queries, >=250 ms acquisition delay, request
spacing, relocation after a lower-NAME conflict, clean cancellation before claim,
raw replay and a reproducible baseline diff. The existing passive-vcan cases also
run. These are software observations, not new physical Siren results.

Layouts and socket behavior were checked against the reviewed simulator sources,
[CANboat discovery definitions](https://canboat.github.io/canboat/canboat.html) and
[python-can Bus API](https://python-can.readthedocs.io/en/stable/bus.html). Passing
these tests is not a claim of complete NMEA certification or physical acceptance.
