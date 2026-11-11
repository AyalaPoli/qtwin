"""Conversion between seconds and integer clock cycles.

All scheduling in qtwin uses integer cycles. Seconds appear only at the API edge, and this module is
the single place where they are converted, with one explicit rounding rule: round half to even.
"""

import math
from fractions import Fraction

CLOCK_HZ: int = 250_000_000
"""Default sequencer clock frequency in Hz (250 MHz)."""

NS_PER_CYCLE: int = 4
"""Duration of one cycle at CLOCK_HZ, in ns."""


def seconds_to_cycles(seconds: float, clock_hz: int = CLOCK_HZ) -> int:
    """Convert a duration to the nearest whole number of clock cycles, ties to even.

    seconds: duration in seconds, finite and >= 0.
    clock_hz: clock frequency in Hz, > 0.
    Returns the duration in cycles.
    Raises ValueError for negative or non-finite durations, or a non-positive clock.
    """
    _check_clock(clock_hz)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"seconds must be finite and >= 0, got {seconds!r}")
    # Why str(): a float like 6e-9 is stored as a binary value slightly below 6e-9, so the exact
    # product would be just under 1.5 cycles and round to 1. str() gives the shortest decimal that
    # maps to that float ("6e-09"), i.e. the value the caller wrote, so 1.5 is a true tie -> 2.
    # Fraction keeps the multiplication exact; round() on a Fraction rounds half to even.
    return round(Fraction(str(seconds)) * clock_hz)


def cycles_to_seconds(cycles: int, clock_hz: int = CLOCK_HZ) -> float:
    """Convert a whole number of clock cycles to seconds (for display and reporting only).

    cycles: duration in cycles, >= 0.
    clock_hz: clock frequency in Hz, > 0.
    Returns the duration in seconds.
    Raises ValueError for negative cycles or a non-positive clock.
    """
    _check_clock(clock_hz)
    if cycles < 0:
        raise ValueError(f"cycles must be >= 0, got {cycles}")
    return cycles / clock_hz


def _check_clock(clock_hz: int) -> None:
    if clock_hz <= 0:
        raise ValueError(f"clock_hz must be > 0, got {clock_hz}")
