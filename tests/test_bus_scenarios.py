"""M5 scenarios: bus_jitter, underrun, overflow, with hand-computed checks.

underrun by hand: L=10, J=0, lead=100, host_gap=40, ts_i = 10_000 + 20 i.
start_i = max(ts_i - 100, start_{i-1} + 40) = 9_900 + 40 i; the 4th word leaves at start_i + 3 and
lands 10 cycles later: commit_i = 9_913 + 40 i. On time while commit_i <= ts_i, i.e. 20 i <= 87,
so commands 0..4 are on time and command 5 (ts 10_100) is the first miss: commit 10_113.
Command 4 ran at 10_080, so the FIFO is empty then: UNDERRUN at 10_113, then every 40 cycles.
"""

import dataclasses

import pytest

from qtwin.device import registers as r
from qtwin.driver.base import margin_cycles
from qtwin.scenarios import (
    SCENARIOS,
    BusConfig,
    StreamConfig,
    overflow_commands,
    run,
    run_scenario,
    stream_commands,
    underrun_commands,
)

UNDERRUN_MISSES = range(5, len(underrun_commands()))


def test_configurations() -> None:
    assert SCENARIOS["bus_jitter"].bus == BusConfig(20, 30, seed=1)
    assert SCENARIOS["bus_jitter"].stream == StreamConfig(lead_cycles=60)
    assert SCENARIOS["underrun"].bus == BusConfig(10, 0)
    assert SCENARIOS["underrun"].stream == StreamConfig(lead_cycles=100, host_gap_cycles=40)
    assert SCENARIOS["overflow"].stream is None
    assert len(SCENARIOS["overflow"].commands) == r.FIFO_CAPACITY + 6


def test_stream_commands_are_100_cycles_apart_on_every_output_lane() -> None:
    cmds = stream_commands()
    assert len(cmds) == 40
    assert [c.timestamp_cycles for c in cmds] == [10_000 + 100 * i for i in range(40)]
    assert len({c.target for c in cmds}) == 8


# --- bus_jitter: buffering absorbs jitter ---


def test_bus_jitter_margin_is_6_cycles() -> None:
    s = SCENARIOS["bus_jitter"]
    assert s.stream is not None
    margin = margin_cycles(s.stream.lead_cycles, s.bus.bus_latency_cycles, s.bus.max_jitter_cycles)
    assert margin == 6


def test_bus_jitter_trace_is_bit_identical_to_ideal() -> None:
    # KEY property: jitter inside the margin costs nothing at the outputs.
    result = run(SCENARIOS["bus_jitter"])
    assert result.edges == run_scenario(stream_commands())
    assert result.status == 0


def test_bus_jitter_really_jitters() -> None:
    delays = {w.arrival_cycle - w.issue_cycle for w in run(SCENARIOS["bus_jitter"]).bus_writes}
    assert min(delays) == 20
    assert max(delays) == 50


def test_same_seed_same_jitter_and_trace_different_seed_different_jitter() -> None:
    s = SCENARIOS["bus_jitter"]
    other = dataclasses.replace(s, bus=dataclasses.replace(s.bus, seed=2))
    a, b, c = run(s), run(s), run(other)
    assert a == b
    assert [w.jitter_cycles for w in a.bus_writes] != [w.jitter_cycles for w in c.bus_writes]
    # Different jitter, same outputs: still inside the margin.
    assert a.edges == c.edges


def test_bus_jitter_with_too_little_lead_misses() -> None:
    s = SCENARIOS["bus_jitter"]
    short = dataclasses.replace(s, stream=StreamConfig(lead_cycles=30))  # margin -24
    result = run(short)
    assert result.status & r.STATUS_LATE
    assert result.status & r.STATUS_UNDERRUN
    assert result.edges != run_scenario(stream_commands())


# --- underrun: the host refills slower than the sequencer consumes ---


def test_underrun_flags_each_miss_at_its_exact_commit_cycle() -> None:
    result = run(SCENARIOS["underrun"])
    expected: list[tuple[int, int]] = []
    for i in UNDERRUN_MISSES:
        expected += [(9_913 + 40 * i, r.STATUS_UNDERRUN), (9_913 + 40 * i, r.STATUS_LATE)]
    assert result.status_events == tuple(expected)
    assert result.status == r.STATUS_UNDERRUN | r.STATUS_LATE


def test_underrun_edges_by_hand() -> None:
    edges = run(SCENARIOS["underrun"]).edges
    for i, e in enumerate(edges):
        ts = 10_000 + 20 * i
        assert e.ideal_cycle == ts
        assert e.actual_cycle == (ts if i < 5 else 9_913 + 40 * i)


def test_underrun_fifo_fills_while_the_host_is_ahead_then_drains() -> None:
    # Commits at 9_913, 9_953, 9_993 come before ts 10_000: three queued at once (the lead at
    # work). From then on the host loses 20 cycles per command and the FIFO runs dry.
    levels = run(SCENARIOS["underrun"]).fifo_levels
    assert max(level for _, level in levels) == 3
    assert (9_993, 3) in levels
    for i in UNDERRUN_MISSES:
        before = [level for cycle, level in levels if cycle < 9_913 + 40 * i]
        assert before[-1] == 0


# --- overflow: a burst larger than CAPACITY ---


def test_overflow_drops_the_burst_tail_and_counts_it() -> None:
    result = run(SCENARIOS["overflow"])
    assert result.dropped == 6
    assert result.status == r.STATUS_OVERFLOW
    assert result.status_events == ((0, r.STATUS_OVERFLOW),) * 6


def test_overflow_leaves_the_first_64_commands_unaffected() -> None:
    first = overflow_commands()[: r.FIFO_CAPACITY]
    assert run(SCENARIOS["overflow"]).edges == run_scenario(first)


@pytest.mark.parametrize("name", ["ideal", "skew_some", "drift_some", "mixed", "one_both"])
def test_m4_scenarios_report_no_errors(name: str) -> None:
    result = run(SCENARIOS[name])
    assert (result.status, result.dropped) == (0, 0)
