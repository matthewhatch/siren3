# Phase 3: passive SocketCAN capture

Phase 3 software and virtual CAN validation are implemented. The user subsequently
reported that passive capture on physical `can1` worked after enabling kernel
listen-only. No raw capture or independent electrical-silence measurements were
provided for review; those acceptance details remain unverified here. No Siren
interaction is claimed from vcan.

## Install and use

Linux SocketCAN, iproute2 (`ip`), Python 3.11+, and the optional hardware dependency
are required. Offline workflows still have no runtime dependency on python-can.

```sh
.venv/bin/python -m pip install -e '.[hardware]'
# Explicit capture on an existing UP virtual CAN link:
.venv/bin/siren-mapper capture --channel vcan0 --listen-only --duration 10 --output /tmp/siren-vcan-001.candump
.venv/bin/siren-mapper report /tmp/siren-vcan-001.candump --format markdown
```

`--channel`, `--listen-only` and `--output` are required. The finite `--duration`
limit defaults to 60 seconds and must be positive; optional `--count N` stops
after N recorded frames. Quiet captures end at the duration limit. SIGINT (Ctrl-C)
and SIGTERM stop promptly, with receive waits capped at 100 ms. They exit 0 after
flushing frames, closing the receiver and writing final metadata. Operational
failures exit 2 with a diagnostic; partial raw evidence is retained. Forced SIGKILL,
power loss and disk failures cannot guarantee final metadata or complete evidence.

Start/stop messages go to stderr. The final JSON summary goes to stdout. Raw output
uses candump -L style, including six-decimal receive timestamps, channel, ID,
payload and T/R flag. Precision is limited by python-can's floating-point receive
timestamp and microsecond formatting. T/R flags retain local-origin information
but do not prove a remote device responded or processed a message.

The raw file and `<output>.json` sidecar use exclusive creation. Existing paths,
including symlinks, are never overwritten. The sidecar is reserved before opening
the raw file or socket; an output-path collision can leave a diagnostic sidecar
with `capture_created: false` and a null hash. A startup failure after raw creation
retains an empty capture and its hash. Use a new output name for each attempt.
The parent directory must already exist. Do not use shell redirection onto raw files.

Final metadata includes interface settings observed before opening, start/end UTC,
limits, frame counts, unsupported-frame count, stop reason, actual file SHA-256,
and uncertainty notes. A successful zero-frame capture has an empty-file hash;
it is not evidence of device absence. The sidecar is finalized on normal/error
cleanup; it is not a crash-proof live journal. No receive-drop counter is collected,
so capture completeness remains unknown.

Classical CAN traffic is retained without a PGN/source filter. Standard-ID, RTR,
and error frames are recorded and counted as unsupported by offline NMEA decoding.
CAN FD reception is disabled; any unexpected FD message surfaced by an adapter is
retained in candump FD notation and rejected by the offline reader. Raw metadata
and file hashes let such anomalies be investigated without silently normalizing
frames. This phase does not expand the phase 1 protocol formats.

## Interface gates and passive behavior

Before opening a CAN socket, the mapper executes only this read-only inspection:

```sh
ip -json -details link show dev can1
```

It requires an exact interface name, UP flag and verified `info_kind`:

- `vcan` is a virtual link, regardless of name. It needs no physical bitrate or
  termination. Virtual tests refuse links whose kind is anything else.
- Physical `can` requires reported bitrate 250000, current `ctrlmode` containing
  `LISTEN-ONLY`, a usable controller state, and no LOOPBACK, FD or PRESUME-ACK mode.
  The user must supply `--isolated-bench`, attesting separation from operational
  vessel networks and verified wiring/termination. Software cannot measure those
  electrical conditions.
- Missing/ambiguous metadata, unavailable `ip`, down links, wrong bitrate, unknown
  link kinds and missing kernel listen-only mode are rejected before output/socket
  creation. Naming a physical link `vcan0` does not bypass the physical gates.

The mapper never executes link setup commands, starts a simulator, claims a source
address or calls a transmit method. python-can is opened with explicit SocketCAN,
configuration-file/environment overrides disabled, no own-message reception,
local loopback disabled and FD disabled. This socket configuration does not itself
make a physical controller electrically silent: the pre-existing kernel LISTEN-ONLY
mode provides that requirement. Settings are verified once; another process could
reconfigure the interface afterward. Keep the selected link under exclusive
administrative control throughout the capture.

## User-run isolated physical bench smoke test (pending)

Run only on the owned, isolated, correctly terminated 250 kbit/s NMEA 2000 bench.
Confirm wiring, power and measured termination separately, and ensure the selected
adapter supports true listen-only. Configure the link externally before invoking
the mapper; it never changes an interface. Do not run on an operational boat network.

1. Inspect `can1` with the read-only command above. Confirm it is UP at 250000
   bit/s and has active kernel `LISTEN-ONLY`. The flag acknowledges actual electrical
   and isolation checks; it does not replace them.
2. Create a new evidence directory and choose unused output names:

   ```sh
   mkdir -p captures
   .venv/bin/siren-mapper capture --channel can1 --listen-only --isolated-bench --duration 75 --output captures/bench-listen-001.candump
   ```

3. On a second attempt with a new filename, press Ctrl-C during capture. Confirm
   clean exit, stop reason `SIGINT`, closed receiver and readable metadata. SIGTERM
   is also supported. No mapper transmission should appear in independent bus
   observation; verify controller listen-only state is unchanged afterward.
4. Replay the preserved data:

   ```sh
   .venv/bin/siren-mapper inventory captures/bench-listen-001.candump
   .venv/bin/siren-mapper report captures/bench-listen-001.candump --format markdown
   ```

5. Record adapter/driver, interface metadata, bitrate, termination measurement,
   bench topology, capture SHA-256, duration, stop reason, errors and evidence
   line references in the findings ledger. Treat local echo separately from
   apparent source-5 traffic. The known seven-byte heartbeat may produce offline
   decode exit 1; retain that anomaly without padding its payload.

A listen-only controller does not provide bus ACKs. On a bench with only a single
transmitter, missing ACKs can affect its transmission behavior; silence or retries
cannot establish Siren application behavior. This procedure does not authorize
starting an active peer or discovery requests. Those belong to a separate explicit
hardware-transmission mode documented in [phase 4](active-discovery.md).

## Automated validation

```sh
# Default suite never opens any interface (all opt-in integration cases skip):
.venv/bin/python -m pytest -q
# Explicit opt-in to an already-created virtual CAN interface:
SIREN_MAPPER_VCAN=vcan0 .venv/bin/python -m pytest -q tests/test_vcan_capture.py
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests
```

Virtual tests inspect the selected link kind before opening any sockets or sending.
A separate test peer sends one synthetic ISO request from source 219 on verified
vcan, the mapper captures it without TX, replay verifies provenance/fields, and the
peer checks for unexpected received traffic. Two further subprocess tests validate
idle SIGINT/SIGTERM shutdown. Keep this vcan link quiet during tests; unrelated
traffic correctly causes a failure rather than being attributed to the mapper.
Tests never create/reconfigure a link or open a physical interface. Mocked tests
cover physical safety gates, wrong/missing metadata, no-send behavior, timeout,
interrupts, receiver errors, shutdown errors and exclusive-file preservation.

References checked for this implementation:
[python-can SocketCAN](https://python-can.readthedocs.io/en/stable/interfaces/socketcan.html),
[Linux SocketCAN](https://docs.kernel.org/networking/can.html), and
[iproute2 CAN metadata implementation](https://github.com/iproute2/iproute2/blob/main/ip/iplink_can.c).
These describe software behavior; they do not establish physical bench acceptance.
