"""Interrupt driver (M7): FIFO_LOW refill, SNSPD detection, and click -> AOM feedback."""

from collections.abc import Sequence

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.snspd import seeded_clicks
from qtwin.driver.base import DriverLog
from qtwin.driver.interrupt import (
    Feedback,
    InterruptDriver,
    min_feedback_latency,
    min_watermark,
)
from qtwin.scope import edge_delay

LATENCY = 20
IRQ_LATENCY = 200
DECIDE = 20
CLICKS = seeded_clicks(2, 5_000, 20, 20_000, 60_000)
GPIOS = [r.addr(f"GPIO{i}.LEVEL") for i in range(4)]
STATUS = r.addr("SYSTEM.STATUS")


def stream_of(count: int, spacing: int) -> list[r.Command]:
    return [r.Command(10_000 + spacing * k, GPIOS[k % 4], k) for k in range(count)]


def run_interrupt(
    commands: Sequence[r.Command] = (),
    clicks: Sequence[int] = (),
    until: int = 100_000,
    feedback: Feedback | None = None,
    low_watermark: int = r.FIFO_LOW_WATERMARK_RESET,
    jitter: int = 0,
) -> tuple[Device, Bus, InterruptDriver, DriverLog]:
    device = Device(clicks={"SNSPD0": clicks})
    bus = Bus(device, bus_latency_cycles=LATENCY, max_jitter_cycles=jitter, seed=5)
    driver = InterruptDriver(
        bus,
        device.run_until,
        device.run_until_irq,
        irq_latency_cycles=IRQ_LATENCY,
        low_watermark=low_watermark,
        feedback=feedback,
    )
    log = driver.run(commands, until_cycle=until)
    device.run_until(max(device.clock.now, until))
    return device, bus, driver, log


# --- Formulas ---


def test_min_feedback_latency_adds_up_the_isr_path() -> None:
    # irq 200 + IRQ_STATUS 20 + ack flush 20 + COUNT 20 + LAST_TS 20 + decide 20
    # + last word leaves 3 cycles after the first + its bus latency 20.
    assert min_feedback_latency(IRQ_LATENCY, LATENCY, 0, DECIDE) == 323
    # Jitter counts twice: on the ack flush and on the command's last word.
    assert min_feedback_latency(IRQ_LATENCY, LATENCY, 10, DECIDE) == 343


def test_min_watermark() -> None:
    # Reaction 200 + 3 * 20 + 3 = 263 cycles, one command per 50 cycles: ceil(5.26) = 6.
    assert min_watermark(IRQ_LATENCY, LATENCY, 0, cycles_per_command=50) == 6
    assert min_watermark(IRQ_LATENCY, LATENCY, 0, cycles_per_command=263) == 1


def test_feedback_budget_below_the_minimum_raises() -> None:
    device = Device()
    with pytest.raises(ValueError):
        InterruptDriver(
            Bus(device, bus_latency_cycles=LATENCY),
            device.run_until,
            device.run_until_irq,
            irq_latency_cycles=IRQ_LATENCY,
            feedback=Feedback(feedback_latency_cycles=322, decide_cycles=DECIDE),
        )


# --- Detection ---


def test_irq_fires_at_each_click_not_before() -> None:
    _, _, driver, _ = run_interrupt(clicks=CLICKS, until=max(CLICKS) + 1_000)
    assert driver.irq_cycles == CLICKS


def test_interrupt_latency_is_constant() -> None:
    _, _, _, log = run_interrupt(clicks=CLICKS, until=max(CLICKS) + 1_000)
    latencies = [d - c for d, c in zip(log.detections, CLICKS, strict=True)]
    assert latencies == [IRQ_LATENCY + LATENCY] * len(CLICKS)


def test_interrupt_reads_only_when_something_happens() -> None:
    # Startup: 2 reads for the clock, 1 for the initial COUNT. Then IRQ_STATUS, COUNT, LAST_TS
    # per click, and nothing at all in between.
    _, bus, _, _ = run_interrupt(clicks=CLICKS, until=max(CLICKS) + 1_000)
    assert bus.read_count == 3 + 3 * len(CLICKS)


def test_the_isr_acknowledges_each_click() -> None:
    device, bus, _, _ = run_interrupt(clicks=CLICKS, until=max(CLICKS) + 1_000)
    assert bus.read32(r.addr("SYSTEM.IRQ_STATUS")) == 0
    assert device.irq_line is False


# --- Feedback ---


@pytest.mark.parametrize("jitter", [0, 10])
def test_feedback_lands_exactly_feedback_latency_after_each_click(jitter: int) -> None:
    budget = 400
    fb = Feedback(feedback_latency_cycles=budget, decide_cycles=DECIDE)
    device, bus, _, log = run_interrupt(
        clicks=CLICKS, until=max(CLICKS) + 1_000, feedback=fb, jitter=jitter
    )
    assert edge_delay(device.scope.edges, "SNSPD0", "AOM0") == [budget] * len(CLICKS)
    assert bus.read32(STATUS) == 0  # no late command, even with jitter
    assert log.pushed == len(CLICKS)


def test_feedback_decision_toggles_aom0_enable_with_count_parity() -> None:
    fb = Feedback(feedback_latency_cycles=400, decide_cycles=DECIDE)
    device, _, _, _ = run_interrupt(clicks=CLICKS, until=max(CLICKS) + 1_000, feedback=fb)
    aom = [e for e in device.scope.edges if e.channel == "AOM0.ENABLE"]
    assert [e.value for e in aom] == [(k + 1) % 2 for k in range(len(CLICKS))]


def test_feedback_works_across_the_last_ts_wrap() -> None:
    click = 2**32 - 100  # the ISR reads LAST_TS after the 32-bit wrap
    fb = Feedback(feedback_latency_cycles=400, decide_cycles=DECIDE)
    device, _, _, _ = run_interrupt(clicks=(click,), until=click + 1_000, feedback=fb)
    assert edge_delay(device.scope.edges, "SNSPD0", "AOM0") == [400]


# --- Refill on FIFO_LOW ---


def test_irq_fires_at_each_watermark_crossing() -> None:
    cmds = stream_of(300, 50)
    device, bus, driver, _ = run_interrupt(cmds, until=cmds[-1].timestamp_cycles)
    crossings = [c for c, m in device.irq_events if m & r.IRQ_FIFO_LOW]
    assert driver.irq_cycles == tuple(crossings)
    watermark = r.FIFO_LOW_WATERMARK_RESET
    assert all((c, watermark - 1) in device.fifo_levels for c in crossings)
    assert bus.read32(STATUS) == 0
    assert all(e.offset == 0 for e in device.scope.edges)


@pytest.mark.parametrize(("watermark", "underrun"), [(6, False), (5, True)])
def test_watermark_below_the_minimum_underruns(watermark: int, underrun: bool) -> None:
    # min_watermark(200, 20, 0, 50) == 6: one command fewer and the refill lands too late.
    cmds = stream_of(300, 50)
    _, bus, _, _ = run_interrupt(cmds, until=cmds[-1].timestamp_cycles, low_watermark=watermark)
    assert bool(bus.read32(STATUS) & r.STATUS_UNDERRUN) is underrun
