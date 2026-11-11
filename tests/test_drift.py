"""drift_offset: whole cycles of drift after n cycles, truncated toward zero (hand-computed)."""

import pytest

from qtwin.device.clock import drift_offset

U64_MAX = 2**64 - 1


@pytest.mark.parametrize(
    ("n", "ppb", "expected"),
    [
        # +50 ppm: one cycle of slip every 20_000 cycles.
        (19_999, 50_000, 0),  # 0.99995 -> 0
        (20_000, 50_000, 1),  # exactly 1
        (1_000_000, 50_000, 50),  # 4 ms -> 50 cycles
        # Negative drift truncates toward zero too, so it mirrors the positive staircase.
        (19_999, -50_000, 0),  # -0.99995 -> 0, not -1
        (20_000, -50_000, -1),
        (1_000_000, -20_000, -20),
        # +30 ppm (one_both): 250_000 * 30e-6 = 7.5 -> 7
        (250_000, 30_000, 7),
        (0, 50_000, 0),
        (1_000_000, 0, 0),
        # 64-bit scale: (2^64 - 1) * 50_000 / 10^9 = 922_337_203_685_477.58 -> ...477.
        # Python ints don't overflow, so this checks the arithmetic, not a wrap.
        (U64_MAX, 50_000, 922_337_203_685_477),
        (U64_MAX, -50_000, -922_337_203_685_477),
    ],
)
def test_drift_offset_hand_computed(n: int, ppb: int, expected: int) -> None:
    assert drift_offset(n, ppb) == expected


def test_drift_offset_is_symmetric_in_sign() -> None:
    for n in (1, 19_999, 20_000, 123_456_789):
        assert drift_offset(n, -50_000) == -drift_offset(n, 50_000)
