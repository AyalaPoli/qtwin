"""Polling driver: the host checks the device on a fixed schedule, whether anything happened or not.

Every poll_period_cycles the driver reads SNSPD0.COUNT (did the detector click?) and FIFO.LEVEL
(how much room is there?), then tops the FIFO up from its command list. Seen only through
Bus.read32/write32 and the register map, like every driver.

The trade-off it shows:
- Latency: a click is noticed at the next COUNT read, so detection latency is anywhere in
  [0, poll_period_cycles + read cost] depending on luck. Faster polling = lower latency.
- CPU cost: every poll costs 2 bus reads even when nothing happened. Over a run of T cycles
  that is 2 * T / poll_period_cycles reads, however rare the events are.
- Refill: the FIFO must last until the next poll, so a slow poll underruns a fast stream.

Host time is tracked here, not read from the device on every step: each read takes
bus_latency_cycles, each frame CMD_WORDS cycles, and the driver itself chooses when to wait.
"""

from collections.abc import Callable, Sequence

from qtwin.bus import Bus
from qtwin.device.registers import CMD_WORDS, FIFO_CAPACITY, REG_MASK, Command, addr
from qtwin.driver.base import DriverLog, push_frame, read_cycle, start_stream

_COUNT = addr("SNSPD0.COUNT")
_LEVEL = addr("FIFO.LEVEL")


class PollingDriver:
    """Refill and detection by polling.

    bus: the only path to the device.
    wait_until: lets device time pass up to an absolute cycle (the test bench's run_until).
    poll_period_cycles: cycles from one poll start to the next (>= 1). A poll whose refill runs
        past the next start delays it: the host can't start a poll it hasn't finished.
    """

    def __init__(
        self, bus: Bus, wait_until: Callable[[int], None], poll_period_cycles: int
    ) -> None:
        if poll_period_cycles < 1:
            raise ValueError(f"poll_period_cycles must be >= 1, got {poll_period_cycles}")
        self._bus = bus
        self._wait_until = wait_until
        self.poll_period_cycles = poll_period_cycles
        self.polls = 0
        """Polls done so far (each costs 2 reads)."""

    def _read(self, address: int, now: int) -> tuple[int, int]:
        # One register read issued at host time `now`: (value, host time when it returns).
        return self._bus.read32(address), now + self._bus.bus_latency_cycles

    def run(self, commands: Sequence[Command], until_cycle: int) -> DriverLog:
        """Stream `commands` (timestamp order) and watch SNSPD0, polling until `until_cycle`.

        Starts the stream (fill the FIFO, set RUN), then polls at every period start
        <= until_cycle (absolute cycles). Returns when the last poll is done.
        """
        latency = self._bus.bus_latency_cycles
        # The CYCLE_LO read samples at its arrival; the CYCLE_HI read costs one more latency.
        now = read_cycle(self._bus) + latency
        pushed, now = start_stream(self._bus, self._wait_until, commands, now)
        last_count, now = self._read(_COUNT, now)
        detections: list[int] = []
        poll = now
        while poll <= until_cycle:
            self._wait_until(poll)
            self.polls += 1
            # COUNT first: detection latency is what this driver is judged on.
            count, now = self._read(_COUNT, poll)
            # Modulo 2^32, so a wrapped counter still gives the right number of new clicks.
            new_clicks = (count - last_count) & REG_MASK
            # Each click is learned when the read returns; which click is which comes from order.
            detections += [now] * new_clicks
            last_count = count
            level, now = self._read(_LEVEL, now)
            # Frames written earlier have landed long ago when the period is >> bus latency,
            # so LEVEL counts them; room is what's left of the FIFO.
            batch = commands[pushed : pushed + FIFO_CAPACITY - level]
            for cmd in batch:
                push_frame(self._bus, self._wait_until, cmd, now)
                now += CMD_WORDS
            pushed += len(batch)
            poll = max(poll + self.poll_period_cycles, now)
        return DriverLog(detections=tuple(detections), pushed=pushed)
