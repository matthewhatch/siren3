"""Reuse simulator address management; expose no engine or identity responder."""

import hashlib
import inspect
from importlib.metadata import version
from pathlib import Path
from typing import Any


class AddressManager:
    def __init__(self, manufacturer: int, unique_number: int, preferred: int):
        try:
            from nmea_sim.can_id import compose
            from nmea_sim.device import CLAIM_WAIT, Device
            from nmea_sim.device_protocol import Name
        except ImportError as exc:
            raise ValueError(
                "install the reviewed nmea2k-sim checkout in this environment: "
                "pip install -e /path/to/nmea2k-sim"
            ) from exc
        self.compose = compose
        self.claim_wait = CLAIM_WAIT
        self.provenance = {
            "package": "nmea-engine-sim",
            "version": version("nmea-engine-sim"),
            "modules": [],
        }
        for component in (Device, Name, compose):
            path = Path(inspect.getfile(component))
            self.provenance["modules"].append(
                {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        self.device = Device(
            Name(unique_number=unique_number, manufacturer_code=manufacturer),
            preferred_address=preferred,
        )

    @property
    def address(self) -> int:
        return int(self.device.address)

    @property
    def owns_address(self) -> bool:
        return bool(self.device.owns_address)

    @property
    def name(self) -> int:
        return int(self.device.name.value)

    def _claims(self, transmissions: list[Any]) -> list[Any]:
        # Simulator may produce its own heartbeat/product/engine capability replies.
        # Discovery uses only its address manager and suppresses those responses.
        return [item for item in transmissions if item.pgn == 60928]

    def reserve(self, address: int) -> None:
        self.device.occupied.setdefault(address, None)

    def start(self, now: float) -> list[Any]:
        if self.device.address in self.device.occupied:
            for step in range(1, 253):
                address = (self.device.address + step) % 252
                if address not in self.device.occupied:
                    self.device.address = address
                    break
            else:
                raise ValueError("no unoccupied source address after baseline")
        return self._claims(self.device.start(now))

    def claim_sent(self, now: float) -> None:
        if str(self.device.state) == "claiming":
            self.device.claim_deadline = max(self.device.claim_deadline, now + self.claim_wait)

    def tick(self, now: float) -> list[Any]:
        claims = self._claims(self.device.tick(now))
        if str(self.device.state) == "cannot_claim":
            raise ValueError("simulator address manager could not acquire a source address")
        return claims

    def receive(self, message: Any, now: float) -> list[Any]:
        claims = self._claims(self.device.receive(message, now))
        if any(item.source == 254 for item in claims):
            raise ValueError("source addresses exhausted; active discovery stopped")
        return claims
