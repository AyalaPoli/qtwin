"""timebase: seconds <-> integer clock cycles, rounding half-to-even at the API edge."""

import math

import pytest

from qtwin.timebase import CLOCK_HZ, NS_PER_CYCLE, cycles_to_seconds, seconds_to_cycles


def test_clock_constants_are_consistent() -> None:
    assert CLOCK_HZ == 250_000_000
    assert NS_PER_CYCLE == 4
    assert CLOCK_HZ * NS_PER_CYCLE == 1_000_000_000


@pytest.mark.parametrize(
    ("seconds", "cycles"),
    [
        (0.0, 0),
        (4e-9, 1),
        (1e-6, 250),
        (1e-3, 250_000),
        (1.0, 250_000_000),
    ],
)
def test_exact_conversions(seconds: float, cycles: int) -> None:
    assert seconds_to_cycles(seconds) == cycles


def test_result_is_int() -> None:
    assert type(seconds_to_cycles(4e-9)) is int


@pytest.mark.parametrize(
    ("seconds", "cycles"),
    [
        (2e-9, 0),  # 0.5 cycles -> nearest even is 0
        (6e-9, 2),  # 1.5 cycles -> nearest even is 2
        (10e-9, 2),  # 2.5 cycles -> 2 (half-up would give 3; this case tells them apart)
        (14e-9, 4),  # 3.5 cycles -> 4
    ],
)
def test_ties_round_half_to_even(seconds: float, cycles: int) -> None:
    assert seconds_to_cycles(seconds) == cycles


def test_non_ties_round_to_nearest() -> None:
    assert seconds_to_cycles(5e-9) == 1  # 1.25 cycles
    assert seconds_to_cycles(7e-9) == 2  # 1.75 cycles


def test_custom_clock_frequency() -> None:
    assert seconds_to_cycles(1e-6, clock_hz=100_000_000) == 100


@pytest.mark.parametrize("bad", [-1e-9, -1.0, math.nan, math.inf, -math.inf])
def test_seconds_to_cycles_rejects_negative_and_non_finite(bad: float) -> None:
    with pytest.raises(ValueError):
        seconds_to_cycles(bad)


def test_cycles_to_seconds_one_cycle() -> None:
    assert cycles_to_seconds(1) == 4e-9


def test_cycles_to_seconds_rejects_negative() -> None:
    with pytest.raises(ValueError):
        cycles_to_seconds(-1)


@pytest.mark.parametrize("bad_clock", [0, -250_000_000])
def test_rejects_non_positive_clock(bad_clock: int) -> None:
    with pytest.raises(ValueError):
        seconds_to_cycles(1e-6, clock_hz=bad_clock)
    with pytest.raises(ValueError):
        cycles_to_seconds(1, clock_hz=bad_clock)


def test_round_trip_is_exact_on_whole_cycles() -> None:
    samples = [*range(100_001), 2**40, 10**15]
    failures = [n for n in samples if seconds_to_cycles(cycles_to_seconds(n)) != n]
    assert failures == []
