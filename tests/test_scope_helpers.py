"""Scope measurement helpers, checked on hand-built traces with known answers."""

from qtwin.scope import Edge, channel_offsets, edge_delay, offset_vs_time


def e(channel: str, ideal: int, actual: int, value: int = 1) -> Edge:
    return Edge(channel=channel, ideal_cycle=ideal, actual_cycle=actual, value=value)


# Ideal: every output lands exactly when commanded.
IDEAL = (e("AOM0.ENABLE", 100, 100), e("GPIO0.LEVEL", 100, 100), e("GPIO0.LEVEL", 200, 200, 0))

# Fake skew: AOM1 always 3 cycles late, GPIO2 always 1 late, GPIO0 on time.
SKEWED = (
    e("GPIO0.LEVEL", 100, 100),
    e("GPIO2.LEVEL", 100, 101),
    e("AOM1.AMPL", 100, 103),
    e("AOM1.ENABLE", 500, 503),
    e("GPIO2.LEVEL", 500, 501, 0),
)

# Fake drift: GPIO0 slips one more whole cycle every 20_000 cycles (+50 ppm), by hand.
DRIFTING = tuple(
    e("GPIO0.LEVEL", n, n + n // 20_000, n // 20_000 % 2) for n in range(0, 100_000, 20_000)
)


def test_offsets_are_zero_on_the_ideal_trace() -> None:
    assert channel_offsets(IDEAL) == {"AOM0": 0, "GPIO0": 0}


def test_offsets_show_static_skew_per_lane() -> None:
    # AOM1's two edges are on different registers but the same physical lane.
    assert channel_offsets(SKEWED) == {"GPIO0": 0, "GPIO2": 1, "AOM1": 3}


def test_offset_uses_the_earliest_edge_of_a_lane() -> None:
    assert channel_offsets(DRIFTING) == {"GPIO0": 0}


def test_offset_vs_time_is_constant_for_skew() -> None:
    assert offset_vs_time(SKEWED, "AOM1") == [(100, 3), (500, 3)]


def test_offset_vs_time_grows_for_drift() -> None:
    assert offset_vs_time(DRIFTING, "GPIO0") == [
        (0, 0),
        (20_000, 1),
        (40_000, 2),
        (60_000, 3),
        (80_000, 4),
    ]


def test_offset_vs_time_of_an_unused_lane_is_empty() -> None:
    assert offset_vs_time(IDEAL, "SNSPD0") == []


def test_edge_delay_between_lanes_uses_actual_cycles() -> None:
    # GPIO0 at 100 -> next AOM1 edge actually at 103; nothing for AOM1 to wait on after that.
    assert edge_delay(SKEWED, "GPIO0", "AOM1") == [3]


def test_edge_delay_picks_the_first_dst_edge_at_or_after_each_src_edge() -> None:
    trace = (
        e("GPIO0.LEVEL", 10, 10),
        e("AOM0.ENABLE", 10, 10),
        e("GPIO0.LEVEL", 50, 50),
        e("AOM0.ENABLE", 57, 57),
        e("AOM0.ENABLE", 90, 90),
    )
    assert edge_delay(trace, "GPIO0", "AOM0") == [0, 7]


def test_edge_delay_skips_a_src_edge_with_no_later_dst_edge() -> None:
    trace = (e("GPIO0.LEVEL", 10, 10), e("AOM0.ENABLE", 20, 20), e("GPIO0.LEVEL", 30, 30))
    assert edge_delay(trace, "GPIO0", "AOM0") == [10]
