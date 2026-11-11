"""Least-squares line fit (analysis only: floats are allowed here, never in scheduling)."""

import pytest

from qtwin.analysis.stats import linear_fit


def test_fit_recovers_an_exact_line() -> None:
    slope, intercept = linear_fit([0, 10, 20, 30], [2, 7, 12, 17])
    assert slope == pytest.approx(0.5)
    assert intercept == pytest.approx(2.0)


def test_fit_of_a_flat_line_has_zero_slope() -> None:
    assert linear_fit([0, 100, 200], [3, 3, 3]) == pytest.approx((0.0, 3.0))


def test_fit_of_the_drift_staircase_is_about_50_ppm() -> None:
    xs = list(range(0, 1_000_001, 20_000))
    ys = [x // 20_000 for x in xs]
    slope, _ = linear_fit(xs, ys)
    assert slope == pytest.approx(50e-6)


def test_fit_needs_two_distinct_x_values() -> None:
    with pytest.raises(ValueError):
        linear_fit([5], [1])
    with pytest.raises(ValueError):
        linear_fit([5, 5], [1, 2])


def test_fit_needs_equal_lengths() -> None:
    with pytest.raises(ValueError):
        linear_fit([0, 1, 2], [0, 1])
