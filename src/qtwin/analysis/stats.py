"""Statistics for analysis and plots.

Floats are allowed here because nothing in this module feeds back into scheduling: it only
summarises a finished trace for a human.
"""

from collections.abc import Sequence


def linear_fit(xs: Sequence[int], ys: Sequence[int]) -> tuple[float, float]:
    """Ordinary least-squares line y = slope * x + intercept.

    xs: e.g. ideal cycles; ys: e.g. offsets in cycles. The slope is then cycles of slip per cycle,
    so slope * 1e9 is the drift in ppb. Returns (slope, intercept).
    Raises ValueError for unequal lengths or fewer than two distinct x values.
    """
    if len(xs) != len(ys):
        raise ValueError(f"xs and ys differ in length ({len(xs)} vs {len(ys)})")
    n = len(xs)
    mean_x = sum(xs) / n if n else 0.0
    mean_y = sum(ys) / n if n else 0.0
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("need at least two distinct x values to fit a line")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def percentile(values: Sequence[int], p: int) -> int:
    """Nearest-rank p-th percentile (p in 1..100) of integer `values`, e.g. latencies in cycles.

    The smallest value with at least p% of the values at or below it. Always one of the values,
    so a latency percentile stays a whole number of cycles. Integer arithmetic only.
    Raises ValueError for an empty list or p outside 1..100.
    """
    if not values:
        raise ValueError("percentile of an empty list")
    if not 1 <= p <= 100:
        raise ValueError(f"p must be in 1..100, got {p}")
    ordered = sorted(values)
    rank = -(-p * len(ordered) // 100)  # ceil(p * n / 100), 1-based
    return ordered[rank - 1]
