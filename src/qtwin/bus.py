"""The host's only path to the device: 32-bit register reads and writes.

M5 timing model for writes: a write issued at device cycle `issue` reaches the device at
`issue + bus_latency_cycles + jitter`, where jitter is drawn uniformly from 0..max_jitter_cycles
by a seeded generator, so the same seed gives the same jitter sequence. Writes are delivered in
issue order: jitter can hold a write back behind an earlier one but never let it overtake
(see Device.schedule_write). That keeps the 4 CMD_PUSH words of a frame in order.

M7 timing model for reads: a read blocks the host for bus_latency_cycles. Device time runs on
while the request travels, and the register is sampled when it arrives, at issue + latency; the
response path is free [assumed]. Reads draw no jitter [assumed], so adding reads never changes a
seed's write-jitter sequence. Writes still in flight that land during the read are seen by it.

With the defaults (0, 0) every write lands on its issue cycle and reads take no time: the instant
bus of M1-M6.
The driver uses this class and never touches qtwin.device directly.
"""

import random
from dataclasses import dataclass

from qtwin.device.device import Device


@dataclass(frozen=True)
class BusWrite:
    """One write as it crossed the bus. All times are absolute device cycles (4 ns each).

    issue_cycle: when the host made the write32 call.
    arrival_cycle: when the device applied it (>= issue + latency + jitter, later only if held
        back behind an earlier write).
    jitter_cycles: the random draw for this write, 0..max_jitter_cycles.
    """

    issue_cycle: int
    arrival_cycle: int
    jitter_cycles: int
    address: int
    value: int


class Bus:
    """Memory-mapped register bus between the host driver and one Device.

    bus_latency_cycles: fixed delay of every write, and the cost of every read, in cycles (>= 0).
    max_jitter_cycles: extra random delay per write, uniform 0..max_jitter_cycles cycles (>= 0).
    seed: seeds the jitter generator; same seed, same jitter sequence.
    """

    def __init__(
        self,
        device: Device,
        bus_latency_cycles: int = 0,
        max_jitter_cycles: int = 0,
        seed: int = 0,
    ) -> None:
        if bus_latency_cycles < 0 or max_jitter_cycles < 0:
            raise ValueError(
                f"latency and jitter must be >= 0, got {bus_latency_cycles}, {max_jitter_cycles}"
            )
        self._device = device
        self.bus_latency_cycles = bus_latency_cycles
        self.max_jitter_cycles = max_jitter_cycles
        # A private generator, not the global `random` module: nothing else can consume draws
        # from it, so the sequence depends only on the seed and the number of writes.
        self._rng = random.Random(seed)
        self._writes: list[BusWrite] = []
        self._read_count = 0

    @property
    def writes(self) -> tuple[BusWrite, ...]:
        """Every write so far, in issue order (for the jitter histogram and tests)."""
        return tuple(self._writes)

    @property
    def read_count(self) -> int:
        """Reads so far: the host CPU cost M7 compares between polling and interrupts."""
        return self._read_count

    def read32(self, address: int) -> int:
        """Read the 32-bit register at byte `address`. Raises BusError on a rejected access.

        Takes bus_latency_cycles of device time: the value is sampled when the read arrives.
        """
        self._read_count += 1
        # The host is blocked on the bus, so device time passes; whatever happens on the way
        # (a write landing, a command firing, a click) is visible in the sampled value. With
        # latency 0 time is not touched at all, so the instant bus reads exactly as in M1-M6
        # (run_until(now) would apply commands due this cycle before the read).
        if self.bus_latency_cycles > 0:
            self._device.run_until(self._device.clock.now + self.bus_latency_cycles)
        return self._device.read(address)

    def write32(self, address: int, value: int) -> None:
        """Write `value` (0..2^32-1) to the register at byte `address`.

        Raises ValueError for an out-of-range value, BusError on a rejected access. Both are
        checked now, at issue, even when the write lands later. A CMD_PUSH word that completes a
        frame with a bad target raises BusError when it arrives (inside Device.run_until).
        """
        self._device.check_write(address, value)
        issue = self._device.clock.now
        # Draw even when max is 0 (randint(0, 0) == 0), so every write consumes exactly one draw.
        jitter = self._rng.randint(0, self.max_jitter_cycles)
        arrival = self._device.schedule_write(
            issue + self.bus_latency_cycles + jitter, address, value
        )
        self._writes.append(BusWrite(issue, arrival, jitter, address, value))
