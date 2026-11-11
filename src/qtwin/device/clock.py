"""The device's free-running 64-bit cycle counter.

Time is simulated: the counter moves only when advance() is called (by tests in M1, by the
event-driven sequencer from M2 on). It never reads the wall clock.
"""

COUNTER_MAX: int = 2**64 - 1
"""Largest counter value, in cycles. At 250 MHz that is about 2,339 years."""


class Clock:
    """64-bit cycle counter, in cycles of the device clock (4 ns at 250 MHz)."""

    def __init__(self) -> None:
        self._now = 0

    @property
    def now(self) -> int:
        """Current counter value, in cycles."""
        return self._now

    def advance(self, cycles: int) -> None:
        """Move time forward by `cycles` (>= 0).

        Raises ValueError for a negative step (time never runs backwards), and OverflowError if the
        counter would pass COUNTER_MAX. Real hardware would wrap, but no run of this model gets
        anywhere near 2^64 cycles, so reaching it means a bug. [assumed]
        """
        if cycles < 0:
            raise ValueError(f"cycles must be >= 0, got {cycles}")
        if self._now + cycles > COUNTER_MAX:
            raise OverflowError(f"cycle counter would pass 2^64 - 1 (now={self._now}, +{cycles})")
        self._now += cycles

    def reset(self) -> None:
        """Set the counter back to 0 (CTRL.RESET)."""
        self._now = 0
