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
