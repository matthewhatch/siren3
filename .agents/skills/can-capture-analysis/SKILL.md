---
name: can-capture-analysis
description: Compare CAN logs before and after a Siren 3 Pro test, inventory PGNs, correlate events, and produce evidence-backed findings.
---

1. Preserve original capture files byte-for-byte and record source, timestamps, interface, physical wiring scenario, and software versions.
2. Normalize candump variations without erasing direction or local-echo caveats.
3. Build per-source/per-PGN timelines and baseline-versus-stimulus differences. Reassemble fast packets before interpreting high-level messages.
4. Distinguish observed response, no visible response and inconclusive results; do not overstate causality from timing correlation.
5. Save reproducible commands, exact frames, decoded fields, assumptions and open questions. Export JSON/Markdown reports.
