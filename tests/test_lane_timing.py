"""Per-lane output timing: static skew, SKEW_TRIM and drift, applied after the sequencer."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device, LaneTiming
from qtwin.device.registers import Command
from qtwin.scope import Edge

T = 100


def run_one(device: Device, cmd: Command) -> Edge:
    """Push one command over the bus, run to its timestamp, return its single edge."""
    bus = Bus(device)
    for word in r.encode_command(cmd):
        bus.write32(r.addr("FIFO.CMD_PUSH"), word)
    bus.write32(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    device.run_until(cmd.timestamp_cycles)
    (edge,) = device.scope.edges
    return edge


def test_negative_skew_is_rejected() -> None:
    # A delay line can only delay; an output can't change before it was commanded.
    with pytest.raises(ValueError):
        LaneTiming(skew_cycles=-1)


def test_unknown_lane_is_rejected() -> None:
    with pytest.raises(ValueError):
        Device(timing={"AOM9": LaneTiming(skew_cycles=1)})


@pytest.mark.parametrize("k", [1, 3])
def test_skew_shifts_an_edge_by_exactly_k_cycles(k: int) -> None:
    device = Device(timing={"GPIO0": LaneTiming(skew_cycles=k)})
    edge = run_one(device, Command(T, r.addr("GPIO0.LEVEL"), 1))
    assert (edge.ideal_cycle, edge.actual_cycle) == (T, T + k)


def test_skew_on_one_lane_leaves_others_alone() -> None:
    device = Device(timing={"GPIO0": LaneTiming(skew_cycles=3)})
    edge = run_one(device, Command(T, r.addr("GPIO1.LEVEL"), 1))
    assert edge.actual_cycle == T


def test_all_aom_fields_share_the_lane_skew() -> None:
    device = Device(timing={"AOM1": LaneTiming(skew_cycles=3)})
    edge = run_one(device, Command(T, r.addr("AOM1.AMPL"), 5))
    assert edge.actual_cycle == T + 3


def test_skew_trim_written_over_the_bus_adds_delay() -> None:
    device = Device(timing={"GPIO0": LaneTiming(skew_cycles=3)})
    Bus(device).write32(r.addr("GPIO0.SKEW_TRIM"), 2)
    edge = run_one(device, Command(T, r.addr("GPIO0.LEVEL"), 1))
    assert edge.actual_cycle == T + 3 + 2


def test_register_takes_the_value_at_the_ideal_cycle() -> None:
    # The delay line sits after the register: the sequencer still acts at T.
    device = Device(timing={"GPIO0": LaneTiming(skew_cycles=3)})
    run_one(device, Command(T, r.addr("GPIO0.LEVEL"), 1))
    assert device.clock.now == T
    assert Bus(device).read32(r.addr("GPIO0.LEVEL")) == 1


@pytest.mark.parametrize(("ppb", "expected"), [(50_000, 20_001), (-50_000, 19_999)])
def test_drift_adds_whole_cycle_slips(ppb: int, expected: int) -> None:
    device = Device(timing={"GPIO0": LaneTiming(drift_ppb=ppb)})
    edge = run_one(device, Command(20_000, r.addr("GPIO0.LEVEL"), 1))
    assert edge.actual_cycle == expected
