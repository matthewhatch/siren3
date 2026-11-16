---
name: nmea2000-protocol-analysis
description: Decode or implement NMEA 2000 CAN, fast-packet reassembly, PGN field layouts, or Siren protocol tests from captured frames.
---

1. Work from raw CAN captures and verified PGN specifications. Separate raw frame facts from interpretations.
2. Parse 29-bit CAN identifiers correctly, including PDU1 destination-dependent PGNs and PDU2 PGNs.
3. For fast packets, key in-flight reassembly on sender, PGN, sequence identifier and time window. Validate declared payload size, indexes, order, duplicates, and timeouts.
4. Decode little-endian and text fields with explicit tests; retain unknown/reserved bytes rather than filling them with guesses.
5. Use `docs/known-findings.md` as a regression fixture, not as infallible protocol truth. Report contradictions.
6. Produce deterministic decoded JSON and tests for malformed/missing frames.
