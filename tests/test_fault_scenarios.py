"""M4 scenarios: registry, command lists, and hand-computed edges per scenario.

The golden files detect any change; these hand-computed edges are what show the values are right.
"""

import pytest

from qtwin.device import registers as r
from qtwin.device.clock import drift_offset
from qtwin.device.device import LaneTiming
from qtwin.scenarios import SCENARIOS, ideal_commands, long_commands, run_scenario
from qtwin.scope import LANES, Edge, component


def run(name: str) -> tuple[Edge, ...]:
    s = SCENARIOS[name]
    return run_scenario(s.commands, s.timing)


def actual_of(edges: tuple[Edge, ...], channel: str, ideal: int) -> int:
    (match,) = [e for e in edges if e.channel == channel and e.ideal_cycle == ideal]
    return match.actual_cycle


def test_registry_matches_the_plan_table() -> None:
    assert list(SCENARIOS) == ["ideal", "skew_some", "drift_some", "mixed", "one_both"]
    assert all(s.name == name for name, s in SCENARIOS.items())


def test_scenario_configurations() -> None:
    assert SCENARIOS["ideal"].timing == {}
    assert SCENARIOS["skew_some"].timing == {
        "AOM1": LaneTiming(skew_cycles=3),
        "GPIO2": LaneTiming(skew_cycles=1),
        "SNSPD0": LaneTiming(skew_cycles=2),  # stored; no SNSPD edges until M7
    }
    assert SCENARIOS["drift_some"].timing == {
        "AOM3": LaneTiming(drift_ppb=50_000),
        "GPIO0": LaneTiming(drift_ppb=-20_000),
    }
    assert SCENARIOS["mixed"].timing == {
        "AOM1": LaneTiming(skew_cycles=3),
        "GPIO2": LaneTiming(skew_cycles=1),
        "AOM3": LaneTiming(drift_ppb=50_000),
        "GPIO0": LaneTiming(drift_ppb=-20_000),
    }
    assert SCENARIOS["one_both"].timing == {"AOM2": LaneTiming(skew_cycles=2, drift_ppb=30_000)}


def test_ideal_scenario_keeps_the_short_m3_list() -> None:
    # Keeps tests/golden/ideal.json unchanged.
    assert SCENARIOS["ideal"].commands == tuple(ideal_commands())


@pytest.mark.parametrize("name", SCENARIOS)
def test_commands_are_ordered_and_fit_the_fifo(name: str) -> None:
    stamps = [c.timestamp_cycles for c in SCENARIOS[name].commands]
    assert stamps == sorted(stamps)
    assert len(stamps) <= r.FIFO_CAPACITY


def test_long_commands_span_a_million_cycles_on_every_output_lane() -> None:
    cmds = long_commands()
    assert max(c.timestamp_cycles for c in cmds) == 1_000_000
    late = {component(r.BY_ADDRESS[c.target].name) for c in cmds if c.timestamp_cycles >= 250_000}
    assert late == {lane for lane in LANES if not lane.startswith("SNSPD")}


def test_long_commands_without_faults_have_zero_offset() -> None:
    edges = run_scenario(long_commands())
    assert len(edges) == len(long_commands())
    assert all(e.offset == 0 for e in edges)


@pytest.mark.parametrize(
    ("name", "channel", "ideal", "actual"),
    [
        ("ideal", "GPIO1.LEVEL", 2_000, 2_000),
        ("skew_some", "AOM1.ENABLE", 1_250, 1_253),
        ("skew_some", "GPIO2.LEVEL", 2_500, 2_501),
        ("skew_some", "AOM0.ENABLE", 1_000, 1_000),
        ("drift_some", "AOM3.ENABLE", 1_750, 1_750),  # 0.0875 cycles -> 0
        ("drift_some", "AOM3.PHASE", 750_000, 750_037),  # 37.5 -> 37
        ("drift_some", "AOM3.PHASE", 1_000_000, 1_000_050),
        ("drift_some", "GPIO0.LEVEL", 750_000, 749_985),
        ("drift_some", "GPIO0.LEVEL", 1_000_000, 999_980),
        ("mixed", "AOM1.PHASE", 1_000_000, 1_000_003),
        ("mixed", "GPIO2.LEVEL", 1_000_000, 1_000_001),
        ("mixed", "AOM3.PHASE", 1_000_000, 1_000_050),
        ("mixed", "GPIO0.LEVEL", 1_000_000, 999_980),
        ("mixed", "AOM0.PHASE", 1_000_000, 1_000_000),
        ("one_both", "AOM2.FREQ_WORD", 0, 2),
        ("one_both", "AOM2.ENABLE", 1_500, 1_502),
        ("one_both", "AOM2.PHASE", 250_000, 250_009),  # 2 + 7 (7.5 -> 7)
        ("one_both", "AOM2.PHASE", 1_000_000, 1_000_032),  # 2 + 30
    ],
)
def test_hand_computed_edge(name: str, channel: str, ideal: int, actual: int) -> None:
    assert actual_of(run(name), channel, ideal) == actual


def test_one_both_offset_is_skew_plus_drift_on_every_edge() -> None:
    aom2 = [e for e in run("one_both") if component(e.channel) == "AOM2"]
    assert aom2
    assert all(e.offset == 2 + drift_offset(e.ideal_cycle, 30_000) for e in aom2)


@pytest.mark.parametrize("name", SCENARIOS)
def test_same_input_gives_the_same_trace(name: str) -> None:
    # M4 has no random source, so determinism is simply: run twice, compare.
    assert run(name) == run(name)


def test_skew_can_reorder_edges_across_lanes() -> None:
    # The trace is in issue order (ideal, FIFO ties); actual_cycle need not be sorted.
    # E.g. AOM1.FREQ_WORD ideal 0 -> 3 is listed before AOM2.FREQ_WORD ideal 0 -> 0.
    actuals = [e.actual_cycle for e in run("skew_some")]
    assert actuals != sorted(actuals)


@pytest.mark.parametrize("name", SCENARIOS)
def test_actual_is_monotonic_within_each_lane(name: str) -> None:
    edges = run(name)
    for lane in LANES:
        actuals = [e.actual_cycle for e in edges if component(e.channel) == lane]
        assert actuals == sorted(actuals)
