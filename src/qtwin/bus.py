"""The host's only path to the device: 32-bit register reads and writes.

M1 version: accesses are instantaneous (0 cycles). Named `bus_latency_cycles` and seeded jitter are
added in M5. The driver uses this class and never touches qtwin.device directly.
"""

from qtwin.device.device import Device


class Bus:
    """Memory-mapped register bus between the host driver and one Device."""

    def __init__(self, device: Device) -> None:
        self._device = device

    def read32(self, address: int) -> int:
        """Read the 32-bit register at byte `address`. Raises BusError on a rejected access."""
        return self._device.read(address)

    def write32(self, address: int, value: int) -> None:
        """Write `value` (0..2^32-1) to the register at byte `address`.

        Raises ValueError for an out-of-range value, BusError on a rejected access.
        """
        self._device.write(address, value)
