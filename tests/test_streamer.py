"""Host command streamer (M5): pushes each command lead_cycles before its timestamp.

KEY property: with margin >= -1 (margin = lead - (latency + max jitter + CMD_WORDS)) the Scope trace
is bit-identical to the same commands prefilled over an instant bus; below that, misses appear.
"""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.driver.base import margin_cycles, read_cycle, stream
from qtwin.scenarios import stream_commands
from qtwin.scope import Edge

STATUS = r.addr("SYSTEM.STATUS")
GPIO0 = r.addr("GPIO0.LEVEL")
LATENCY, JITTER = 20, 30
CMDS = stream_commands()  # 40 commands, 100 cycles apart, all 8 output lanes


def streamed(lead: int, seed: int = 1, latency: int = LATENCY, jitter: int = JITTER) -> Device:
    device = Device()
    bus = Bus(device, bus_latency_cycles=latency, max_jitter_cycles=jitter, seed=seed)
    device.write(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    stream(bus, CMDS, device.run_until, lead_cycles=lead)
    device.run_until(CMDS[-1].timestamp_cycles + 1_000)
    return device


def prefilled() -> tuple[Edge, ...]:
    device = Device()
    for cmd in CMDS:
        device.push_command(cmd)
    device.write(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    device.run_until(CMDS[-1].timestamp_cycles + 1_000)
    return device.scope.edges


def test_margin_formula() -> None:
    assert margin_cycles(lead_cycles=60, bus_latency_cycles=20, max_jitter_cycles=30) == 6


def test_read_cycle_reads_the_64_bit_counter() -> None:
    device = Device()
    device.run_until(2**32 + 5)
    assert read_cycle(Bus(device)) == 2**32 + 5


@pytest.mark.parametrize("margin", [6, 0, -1])
def test_trace_is_bit_identical_to_ideal_when_jitter_fits_the_margin(margin: int) -> None:
    lead = margin + LATENCY + JITTER + r.CMD_WORDS
    device = streamed(lead)
    assert device.scope.edges == prefilled()
    assert Bus(device).read32(STATUS) == 0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bit_identical_for_any_seed_at_margin_zero(seed: int) -> None:
    device = streamed(LATENCY + JITTER + r.CMD_WORDS, seed=seed)
    assert device.scope.edges == prefilled()


def test_jitter_above_the_margin_makes_commands_late() -> None:
    # Margin -2: a command misses only if its last words draw near-maximum jitter. With seed 1
    # some do (checked by running); every miss is a late edge and the run reports it.
    device = streamed(LATENCY + JITTER + r.CMD_WORDS - 2)
    late = [e for e in device.scope.edges if e.actual_cycle > e.ideal_cycle]
    assert late
    assert Bus(device).read32(STATUS) & r.STATUS_LATE
    # The FIFO was empty each time (commands are 100 cycles apart), so each miss is an underrun.
    assert Bus(device).read32(STATUS) & r.STATUS_UNDERRUN


def test_start_cycles_follow_lead_then_fall_behind_when_the_host_is_slow() -> None:
    # Commands every 20 cycles, host needs 40 per command: start_i = max(ts_i - 100, prev + 40).
    device = Device()
    cmds = [r.Command(10_000 + 20 * i, GPIO0, i % 2) for i in range(8)]
    starts = stream(Bus(device), cmds, device.run_until, lead_cycles=100, host_gap_cycles=40)
    assert starts == tuple(9_900 + 40 * i for i in range(8))


def test_words_are_written_one_per_cycle_from_the_start_cycle() -> None:
    device = Device()
    bus = Bus(device)
    stream(bus, [r.Command(1_000, GPIO0, 1)], device.run_until, lead_cycles=100)
    assert [w.issue_cycle for w in bus.writes] == [900, 901, 902, 903]


def test_start_is_never_in_the_past() -> None:
    device = Device()
    device.run_until(500)
    bus = Bus(device)
    assert stream(bus, [r.Command(550, GPIO0, 1)], device.run_until, lead_cycles=100) == (500,)


def test_host_gap_shorter_than_a_frame_raises() -> None:
    device = Device()
    with pytest.raises(ValueError):
        stream(Bus(device), [], device.run_until, lead_cycles=100, host_gap_cycles=3)
