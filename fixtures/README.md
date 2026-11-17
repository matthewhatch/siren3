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
