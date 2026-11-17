# Fixture inventory and provenance

The original three capture fixtures' CAN ID/data pairs are verbatim from `docs/known-findings.md`, supplied by the
user from an isolated `can1` bench. Only a `can1 ` prefix and newline delimiters
were added. No timestamps or T/R directions were available; none were invented.
Snippets are separate observations, not one continuous recording. Original
session files and collection times remain unavailable. No credentials or
unrelated device traffic are included; the requested device serial is retained
as regression evidence.

| File | Frames | Contents | SHA-256 |
| --- | ---: | --- | --- |
| product.candump | 20 | Product response, source 5 | f97471c6bd5001507e4e26b036c99963b053d20d512026f8d8744d32eef11449 |
| pgn-lists.candump | 10 | Separate advertised TX/RX lists | eccece5db464ce73b759c8c912b37c469f12c1fd77fcc602efb4e85b4bfc29ab |
| discovery.candump | 6 | Two requests, configuration NACK, two claims, anomalous heartbeat | ec9a1b52138fabfaa4c51c903e820b68725a0616fa1c1d3b672f62c2c7a09afc |

Treat these as immutable source evidence. Tests generate damaged packets and
synthetic timestamps/configuration fields in memory rather than editing captures.
The request/response ordering in discovery.candump organizes ledger examples;
it does not prove timing correlation or a new observed exchange.

## Synthetic interpreter vectors (phase 4a)

`interpreter-synthetic.candump` is a new, explicitly synthetic test fixture, not an
additional Siren capture. Its 11 frames have fabricated timestamps/directions and
known-value vectors for consecutive eight-byte heartbeats, 1200 RPM, signed dynamic
engine values/status bits (four fast-packet fragments), a 50%/1000 L oil tank,
unknown/proprietary bytes and an unsupported ISO transport payload. Engine source
35 and apparent heartbeat/tank source 5 are test addresses only. The original
seven-byte heartbeat remains unchanged in `discovery.candump`.

SHA-256: `577d254e05909d7c3dbb324fceb727e05b8f40f73441ebafbad419ef88caa33c`.
Raw prefixes/fields were generated as standard-layout demonstration vectors, not
observed device responses; see `docs/packet-interpreter.md` and the sensor tests.

## Core discovery/fingerprinting regression sequence

`siren-fingerprint.candump` contains 36 frames arranged from the recorded November
16 snippets: Siren and mapper NAME claims, Product request/twenty-frame reply,
Configuration request/NACK, and PGN-list request/two five-frame role lists. Every
CAN ID/data pair was previously recorded in the findings ledger/original fixtures.
The ordering is curated for regression, **not evidence of a complete continuous
session**. No timestamps or TX/RX flags were invented; profile correlation is
explicitly order-only with timing unknown. The anomalous heartbeat remains in its
original fixture rather than being silently repaired for this sequence.

The five `fingerprinting/` edge-case fixtures are explicitly synthetic. They cover
an unknown source, dropped/reordered Product fragments, a timed window with no
Configuration reply, competing source-5 NAME claims, and duplicate/wrong-source
Product replies. Captured bytes reused in those variants do not make the new
ordering/timing/source conflicts real device observations.

| File | SHA-256 |
| --- | --- |
| siren-fingerprint.candump | `753c3c8afc868f21a9d9ea42bb69e381a7d9031680e702f715d31eaf89d9d37a` |
| fingerprinting/duplicate-unexpected.candump | `61625aa2f6ca56e560ec7a95d36120426ba83040ff9af535710260ebc77f9b21` |
| fingerprinting/malformed-fast-packet.candump | `f415f225e357fbc43fdb9aa2ce130e6227c77b1b1b38d0d5fc2d51fcdb3c0b28` |
| fingerprinting/missing-configuration.candump | `b76c3f599655553f3d7cd19b8ba55ad9f4cb0506b2933c14fbde8c62bfcfdbb6` |
| fingerprinting/source-collision.candump | `aae5d2f3e34d777b0c1631f2ce2a6bca94a8ea010c5a26cc190cd6078ce2764f` |
| fingerprinting/unknown-device.candump | `4a9b50a51efaa8a0961a4ffeb184f7ce723870bc805141b3e1dc65d76074e6d7` |
