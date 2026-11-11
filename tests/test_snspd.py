"""SNSPD click model (M7): seeded click cycles; COUNT, LAST_TS, the IRQ bit and a Scope edge."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import Command
from qtwin.device.snspd import seeded_clicks
from qtwin.scope import Edge

COUNT0 = r.addr("SNSPD0.COUNT")
LAST_TS0 = r.addr("SNSPD0.LAST_TS")
COUNT1 = r.addr("SNSPD1.COUNT")
LAST_TS1 = r.addr("SNSPD1.LAST_TS")
IRQ_STATUS = r.addr("SYSTEM.IRQ_STATUS")
IRQ_ENABLE = r.addr("SYSTEM.IRQ_ENABLE")
GPIO0 = r.addr("GPIO0.LEVEL")


# --- seeded_clicks ---


def test_seeded_clicks_are_deterministic() -> None:
    assert seeded_clicks(7, 1_000, 50, 10, 500) == seeded_clicks(7, 1_000, 50, 10, 500)


def test_different_seeds_give_different_clicks() -> None:
    assert seeded_clicks(7, 1_000, 50, 10, 500) != seeded_clicks(8, 1_000, 50, 10, 500)


def test_seeded_click_gaps_stay_within_bounds() -> None:
    clicks = seeded_clicks(3, 1_000, 200, 10, 500)
    assert len(clicks) == 200
    gaps = [b - a for a, b in zip((1_000, *clicks), clicks, strict=False)]
    assert all(10 <= g <= 500 for g in gaps)
    assert all(isinstance(c, int) for c in clicks)


@pytest.mark.parametrize(
    ("start", "count", "min_gap", "max_gap"),
    [(-1, 5, 1, 2), (0, -1, 1, 2), (0, 5, 0, 2), (0, 5, 3, 2)],
)
def test_seeded_clicks_reject_bad_arguments(
    start: int, count: int, min_gap: int, max_gap: int
) -> None:
    with pytest.raises(ValueError):
        seeded_clicks(0, start, count, min_gap, max_gap)


# --- Device: clicks as a third event source ---


def test_click_updates_count_last_ts_and_irq_on_its_cycle_not_before() -> None:
    device = Device(clicks={"SNSPD0": (300, 700)})
    device.run_until(299)
    assert device.read(COUNT0) == 0
    assert device.irq_events == ()
    device.run_until(300)
    assert device.read(COUNT0) == 1
    assert device.read(LAST_TS0) == 300
    assert device.read(IRQ_STATUS) == r.IRQ_SNSPD0
    assert device.irq_events == ((300, r.IRQ_SNSPD0),)
    device.run_until(1_000)
    assert device.read(COUNT0) == 2
    assert device.read(LAST_TS0) == 700


def test_snspd1_has_its_own_registers_and_irq_bit() -> None:
    device = Device(clicks={"SNSPD1": (50,)})
    device.run_until(100)
    assert (device.read(COUNT0), device.read(COUNT1), device.read(LAST_TS1)) == (0, 1, 50)
    assert device.read(IRQ_STATUS) == r.IRQ_SNSPD1


def test_click_is_not_an_error_flag() -> None:
    device = Device(clicks={"SNSPD0": (10,)})
    device.run_until(20)
    assert device.read(r.addr("SYSTEM.STATUS")) == 0
    assert device.status_events == ()


def test_click_is_recorded_on_the_scope() -> None:
    device = Device(clicks={"SNSPD0": (300, 700)})
    device.run_until(1_000)
    assert device.scope.edges == (
        Edge("SNSPD0.COUNT", 300, 300, 1),
        Edge("SNSPD0.COUNT", 700, 700, 2),
    )


def test_last_ts_keeps_the_low_32_bits_of_the_click_cycle() -> None:
    device = Device(clicks={"SNSPD0": (2**32 + 5,)})
    device.run_until(2**32 + 10)
    assert device.read(LAST_TS0) == 5


def test_clicks_count_while_the_sequencer_is_stopped() -> None:
    # The detector is an input, independent of CTRL.RUN. [assumed]
    device = Device(clicks={"SNSPD0": (10,)})
    device.run_until(20)
    assert device.read(COUNT0) == 1


def test_click_lands_after_a_bus_write_and_before_the_sequencer_on_one_cycle() -> None:
    device = Device(clicks={"SNSPD0": (100,)})
    device.push_command(Command(100, GPIO0, 1))
    device.write(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    device.run_until(100)
    assert [e.channel for e in device.scope.edges] == ["SNSPD0.COUNT", "GPIO0.LEVEL"]


def test_a_slow_read_sees_a_click_during_the_read() -> None:
    device = Device(clicks={"SNSPD0": (105,)})
    bus = Bus(device, bus_latency_cycles=10)
    device.run_until(100)
    assert bus.read32(COUNT0) == 1  # sampled at 110


def test_run_until_irq_stops_at_the_click() -> None:
    device = Device(clicks={"SNSPD0": (300, 700)})
    device.write(IRQ_ENABLE, r.IRQ_SNSPD0)
    assert device.run_until_irq(10_000) == 300
    device.write(IRQ_STATUS, r.IRQ_SNSPD0)  # ISR acknowledges
    assert device.run_until_irq(10_000) == 700


@pytest.mark.parametrize(
    "clicks",
    [{"AOM0": (1,)}, {"SNSPD0": (5, 5)}, {"SNSPD0": (9, 3)}, {"SNSPD0": (-1,)}],
)
def test_bad_click_lists_raise(clicks: dict[str, tuple[int, ...]]) -> None:
    with pytest.raises(ValueError):
        Device(clicks=clicks)
