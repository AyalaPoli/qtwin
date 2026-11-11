"""The ideal scenario: a fixed command list run through the real bus/CMD_PUSH path."""

from qtwin.device import registers as r
from qtwin.scenarios import ideal_commands, run_scenario
from qtwin.scope import LANES, component


def test_ideal_commands_are_in_time_order_and_fit_the_fifo() -> None:
    cmds = ideal_commands()
    stamps = [c.timestamp_cycles for c in cmds]
    # The FIFO never reorders, so an out-of-order list would make commands late.
    assert stamps == sorted(stamps)
    assert len(cmds) <= r.FIFO_CAPACITY


def test_ideal_drives_every_output_lane() -> None:
    lanes = {component(e.channel) for e in run_scenario(ideal_commands())}
    assert lanes == {lane for lane in LANES if not lane.startswith("SNSPD")}


def test_ideal_has_zero_offset_everywhere() -> None:
    edges = run_scenario(ideal_commands())
    assert len(edges) == len(ideal_commands())
    assert all(e.actual_cycle == e.ideal_cycle for e in edges)


def test_ideal_contains_a_same_cycle_tie() -> None:
    stamps = [c.timestamp_cycles for c in ideal_commands()]
    assert len(set(stamps)) < len(stamps)


def test_run_scenario_is_deterministic() -> None:
    assert run_scenario(ideal_commands()) == run_scenario(ideal_commands())
