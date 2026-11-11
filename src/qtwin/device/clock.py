"""The device's free-running 64-bit cycle counter.

Time is simulated: the counter moves only when advance() is called (by tests in M1, by the
event-driven sequencer from M2 on). It never reads the wall clock.
"""

COUNTER_MAX: int = 2**64 - 1
"""Largest counter value, in cycles. At 250 MHz that is about 2,339 years."""

PPB: int = 1_000_000_000
"""Parts per billion in one: drift_ppb / PPB is the fractional frequency error."""


def drift_offset(n: int, drift_ppb: int) -> int:
    """Cycles of accumulated drift after n local cycles. Truncates toward zero.

    n: cycles since reset. drift_ppb: frequency error in parts per billion (50_000 = +50 ppm).
    Integer only, so the result is a staircase of whole-cycle slips: at +50 ppm, cycle 19_999 -> 0,
    cycle 20_000 -> 1. Truncating the magnitude (not floor division) keeps +x and -x symmetric.
    """
    q = abs(n * drift_ppb) // PPB
    return q if n * drift_ppb >= 0 else -q


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
