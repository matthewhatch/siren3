# Offline evidence reports (phase 2)

## Reproduce

From the repository root after installing the development extra:

```sh
.venv/bin/siren-mapper decode fixtures/product.candump
.venv/bin/siren-mapper inventory fixtures/pgn-lists.candump
.venv/bin/siren-mapper diff fixtures/product.candump fixtures/product.candump
.venv/bin/siren-mapper report fixtures/product.candump --format json
.venv/bin/siren-mapper report fixtures/product.candump --format markdown
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests
```

Commands read local files and print to stdout. They create no captures, configure
no interfaces, and open no CAN sockets. Preserve input files as source evidence.
Shell redirection can save stdout to a **different** path; do not redirect onto an
input capture. Exit codes: 0 = input accepted, 1 = parse/assembly/payload errors,
2 = unreadable or non-ASCII capture (or CLI usage error). Diffs with valid but
changed observations return 0. Reports are still emitted on capture errors.

`report` defaults to JSON. Both formats are deterministic for the same path,
bytes and software version. Output contains no current clock, random IDs or
filesystem modification times. File paths intentionally remain in provenance,
so moving identical input files changes path metadata while retaining their hash.
Markdown has source/message tables and the complete structured evidence as an
indented JSON block, including raw lines, confidence and unresolved questions.
Arbitrary capture text cannot introduce Markdown tables or active HTML there.

## Schema version 1

`decode` returns a capture evidence document:

- `capture`: input path, SHA-256 of exact file bytes, and byte count. The file is
  read once; metadata and records refer to those same bytes.
- `raw_frames`: every successfully parsed CAN frame, including orphaned or
  incomplete fragments and malformed protocol payloads. Fields include line,
  original raw line, channel, exact timestamp string or null, direction, CAN ID,
  PGN, source, destination and payload hex. Raw lines exclude line endings;
  the file hash covers the exact bytes including line endings.
- `records`: complete message evidence. `message-N` IDs follow completion order
  and are local to this capture; resolve them together with the input file/hash.
  PGN, source, destination, channel, priority, decoded fields and payload are
  retained. `timestamp` and `can_id` refer to the final frame; `frames` retains
  each constituent frame's ID, timestamp, direction and raw line.
- `provenance` in each record: path, hash and all constituent line numbers.
- `status`: `decoded`, `unknown` (unsupported PGN), or `malformed` (complete payload
  failed validation). Incomplete assemblies have diagnostics but no decoded record.
- `partial_decoded`, when present: separately labeled known-prefix fields from a
  malformed heartbeat. Its status remains `malformed`; these are never substituted
  for a validated complete payload or used to fill missing bytes.
- `classification`: `decoded_fact` only after layout validation; otherwise
  `unknown`. No hypotheses are generated automatically.
- `confidence`: `supported` means a successful offline wire-layout decode;
  `unresolved` means unknown or malformed content. `basis` states the reason.
  These labels do not verify sender identity, standards certification, physical
  bench setup, application processing or causal response.
- `unresolved_questions`: explicit gaps in timing, source ownership, local echo,
  undecoded bytes, malformed payloads and advertised capability versus actual use.
- `errors`: diagnostic, capture line, original line where available, and path/hash
  provenance. EOF diagnostics have null line/raw; the original fragments remain
  in `raw_frames`. Invalid candump lines remain in diagnostic raw text.

`inventory` adds `sources`, sorted by channel/source. Counts distinguish raw
frames from complete messages. `observed_pgns` derives from actual parsed frames;
PGNs appearing inside an advertised list are not counted as observed traffic.
`observations` retains every successfully decoded NAME, product and PGN list,
including conflicting values, in capture order with provenance. Complete message
evidence, raw frames and diagnostics remain available at the document level.
A source address is a grouping key, not a permanent device identifier.

`report` adds a summary and the source inventory to capture evidence. Malformed
heartbeat data is displayed with its error and original bytes; it is never padded
or silently treated as a valid eight-byte heartbeat.

## Diff interpretation

Streams are keyed by channel, source address, destination and PGN. Each side
records frame count, complete-message count, unique payloads, payload frequency
and decode-status counts. Added, removed or changed streams reference complete
message IDs; the output retains both sides' full records and raw frames. An
identical file compared to itself has no changes (unless errors make it inconclusive).

Timestamps, T/R direction, CAN priority, transport sequence and message order are
excluded from equality. Identical payloads with different transport metadata
therefore compare equal; changes in timing/order alone are outside this command's
policy. Different counts may reflect different collection lengths. Rates are not
inferred, and addresses are not automatically matched by NAME across captures.

`observed_change` means at least one compared stream summary differs.
`no_observed_change` means summaries match under this policy, including for empty
inputs; it makes no claim about unobserved behavior. `inconclusive` takes precedence
when either side contains parse, assembly or payload errors, while retaining any
observable differences. The seven-byte heartbeat fixture therefore yields an
inconclusive diff even against itself. These comparisons never establish that a
Siren processed a stimulus or that a recorded TX/RX marking is a remote response.

## Evidence and validation limits

No new device observations were collected. Product and PGN-list examples are the
same user-provided snippets inventoried in `fixtures/README.md`; those lack timestamps.
Changed payloads, timestamps, direction flags, conflicting identities and damaged
packets in tests are synthetic variations, not new Siren behavior.

Hardware tests (physical CAN and vcan) were not run for this phase. Automated tests
exercise deterministic output, exact provenance, malformed/incomplete evidence,
identity separation, comparisons, CLI exit status, source-file preservation and
socket-access prevention. Existing phase 1 protocol regression tests remain enabled.

Human text output is now available through `decode --human`, `replay`, and explicit
live `capture`/`discover` human options. It uses the same packet decode pipeline;
see [packet interpreter](packet-interpreter.md). JSON remains the default for all
pre-existing commands. Sensor PGNs 127488/127489/127505 are now supported read-only,
so their complete payloads are decoded rather than labeled unknown.
