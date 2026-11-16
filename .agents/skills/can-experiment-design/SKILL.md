---
name: can-experiment-design
description: Design or implement controlled live SocketCAN discovery or sensor stimulation experiments for the isolated Siren NMEA 2000 test bench.
---

1. Default to offline vcan testing and replay. Any real CAN transmission requires explicit user-selected isolated-bench hardware mode.
2. Validate channel, bitrate 250000, termination, electrical safety, and separation from operational vessel networks. Fail closed on uncertainty.
3. Require full ISO Address Claim management and source-address conflict detection before sending application PGNs.
4. Set finite duration, bounded transmit rate, logged timestamps, baseline/stimulus/recovery windows, and immediate cancellation.
5. Change one measurement per scenario, prefer realistic engine and fluid values, avoid intentionally destructive fault conditions.
6. Never infer that a Siren consumed a message just because the CAN frame was ACKed, echoed or received by a local socket.
7. Report observed traffic and uncertainty; avoid modifying Siren configuration.
