"""IRQ path (M7): IRQ-only events, the FIFO_LOW watermark crossing, and run_until_irq."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import Command

CTRL = r.addr("SYSTEM.CTRL")
STATUS = r.addr("SYSTEM.STATUS")
IRQ_STATUS = r.addr("SYSTEM.IRQ_STATUS")
IRQ_ENABLE = r.addr("SYSTEM.IRQ_ENABLE")
WATERMARK = r.addr("FIFO.LOW_WATERMARK")
GPIO0 = r.addr("GPIO0.LEVEL")


@pytest.fixture
def device() -> Device:
    return Device()


@pytest.fixture
def bus(device: Device) -> Bus:
    return Bus(device)


def prefill(device: Device, count: int = 20) -> None:
    # Command k (1-based) runs at 100 * k, so the FIFO drops by one every 100 cycles.
    for k in range(1, count + 1):
        device.push_command(Command(100 * k, GPIO0, k % 2))


# --- set_status: STATUS keeps error flags only ---


def test_irq_only_bits_do_not_enter_status(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_LATE | r.IRQ_FIFO_LOW)
    assert bus.read32(STATUS) == r.STATUS_LATE
    assert bus.read32(IRQ_STATUS) == r.IRQ_LATE | r.IRQ_FIFO_LOW


def test_status_events_keep_error_flags_and_irq_events_keep_everything(device: Device) -> None:
    device.clock.advance(7)
    device.set_status(r.IRQ_FIFO_LOW)
    device.set_status(r.STATUS_LATE)
    assert device.status_events == ((7, r.STATUS_LATE),)
    assert device.irq_events == ((7, r.IRQ_FIFO_LOW), (7, r.IRQ_LATE))


# --- FIFO_LOW: raised on the pop that takes LEVEL below LOW_WATERMARK ---


def test_fifo_low_fires_on_the_crossing_pop_not_before(device: Device, bus: Bus) -> None:
    # 20 queued, watermark 16: the 5th pop (cycle 500) takes the level from 16 to 15.
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(499)
    assert device.irq_events == ()
    assert bus.read32(IRQ_STATUS) == 0
    device.run_until(500)
    assert device.irq_events == ((500, r.IRQ_FIFO_LOW),)
    assert bus.read32(r.addr("FIFO.LEVEL")) == r.FIFO_LOW_WATERMARK_RESET - 1


def test_fifo_low_is_an_edge_not_a_level(device: Device, bus: Bus) -> None:
    # Draining on down to 0 stays below the watermark: no further events.
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(10_000)
    assert device.irq_events == ((500, r.IRQ_FIFO_LOW),)


def test_fifo_low_follows_the_watermark_register(device: Device, bus: Bus) -> None:
    bus.write32(WATERMARK, 4)
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(10_000)
    assert device.irq_events == ((1_700, r.IRQ_FIFO_LOW),)  # 17th pop: 4 -> 3


def test_fifo_low_rearms_after_a_refill(device: Device, bus: Bus) -> None:
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(1_500)  # 5 left (1_600 .. 2_000)
    for k in range(20):
        device.push_command(Command(3_000 + 100 * k, GPIO0, 1))  # 25 queued
    device.run_until(10_000)
    # 25 -> 16 takes 9 pops (the old 5, then 3_000 .. 3_300); the pop at 3_400 crosses again.
    assert [cycle for cycle, _ in device.irq_events] == [500, 3_400]


def test_filling_the_fifo_never_raises_fifo_low(device: Device) -> None:
    prefill(device, 40)
    assert device.irq_events == ()


def test_irq_line_rises_only_when_fifo_low_is_enabled(device: Device, bus: Bus) -> None:
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(600)
    assert device.irq_line is False
    bus.write32(IRQ_ENABLE, r.IRQ_FIFO_LOW)
    assert device.irq_line is True


# --- run_until_irq: the test bench's wait_for_irq ---


def test_run_until_irq_stops_on_the_rising_cycle(device: Device, bus: Bus) -> None:
    bus.write32(IRQ_ENABLE, r.IRQ_FIFO_LOW)
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    assert device.run_until_irq(10_000) == 500
    assert device.clock.now == 500
    assert len(device.scope.edges) == 5  # nothing after the IRQ cycle has run yet


def test_run_until_irq_returns_none_at_the_limit_if_nothing_is_enabled(
    device: Device, bus: Bus
) -> None:
    prefill(device)
    bus.write32(CTRL, r.CTRL_RUN)
    assert device.run_until_irq(10_000) is None
    assert device.clock.now == 10_000


def test_run_until_irq_returns_now_if_the_line_is_already_high(device: Device, bus: Bus) -> None:
    bus.write32(IRQ_ENABLE, r.IRQ_LATE)
    device.clock.advance(42)
    device.set_status(r.STATUS_LATE)
    assert device.run_until_irq(10_000) == 42


def test_run_until_irq_sees_an_event_raised_by_a_landing_bus_write(device: Device) -> None:
    # A late command whose frame lands at 50 raises UNDERRUN | LATE on that cycle.
    bus = Bus(device, bus_latency_cycles=50)
    device.write(IRQ_ENABLE, r.IRQ_UNDERRUN)
    device.write(CTRL, r.CTRL_RUN)
    for word in r.encode_command(Command(30, GPIO0, 1)):
        bus.write32(r.addr("FIFO.CMD_PUSH"), word)
    assert device.run_until_irq(1_000) == 50
