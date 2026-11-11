"""Clock: the device's 64-bit free-running cycle counter, advanced explicitly (no wall clock)."""

import pytest

from qtwin.device.clock import COUNTER_MAX, Clock


def test_starts_at_zero() -> None:
    assert Clock().now == 0


def test_advance_adds_cycles() -> None:
    clock = Clock()
    clock.advance(10)
    clock.advance(5)
    assert clock.now == 15


def test_advance_by_zero_is_allowed() -> None:
    clock = Clock()
    clock.advance(0)
    assert clock.now == 0


def test_negative_step_raises() -> None:
    with pytest.raises(ValueError):
        Clock().advance(-1)


def test_counter_is_64_bit() -> None:
    assert COUNTER_MAX == 2**64 - 1


def test_can_reach_the_last_value() -> None:
    clock = Clock()
    clock.advance(COUNTER_MAX)
    assert clock.now == COUNTER_MAX


def test_overflow_raises_and_leaves_the_counter_unchanged() -> None:
    clock = Clock()
    clock.advance(COUNTER_MAX)
    with pytest.raises(OverflowError):
        clock.advance(1)
    assert clock.now == COUNTER_MAX


def test_reset_returns_to_zero() -> None:
    clock = Clock()
    clock.advance(123)
    clock.reset()
    assert clock.now == 0
