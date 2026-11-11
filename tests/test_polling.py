"""Polling driver (M7): refill by reading FIFO.LEVEL, detect clicks by reading SNSPD0.COUNT."""

from collections.abc import Sequence

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.snspd import seeded_clicks
from qtwin.driver.base import DriverLog
from qtwin.driver.polling import PollingDriver

LATENCY = 20
CLICKS = seeded_clicks(1, 5_000, 30, 2_000, 9_000)
GPIOS = [r.addr(f"GPIO{i}.LEVEL") for i in range(4)]


def stream_of(count: int, spacing: int) -> list[r.Command]:
    # Command k at 10_000 + spacing * k, round robin over the GPIOs, value k.
    return [r.Command(10_000 + spacing * k, GPIOS[k % 4], k) for k in range(count)]


def run_polling(
    period: int,
    commands: Sequence[r.Command] = (),
    clicks: Sequence[int] = (),
    until: int = 100_000,
) -> tuple[Device, Bus, PollingDriver, DriverLog]:
    device = Device(clicks={"SNSPD0": clicks})
    bus = Bus(device, bus_latency_cycles=LATENCY)
    driver = PollingDriver(bus, device.run_until, poll_period_cycles=period)
    log = driver.run(commands, until_cycle=until)
    device.run_until(max(device.clock.now, until))
    return device, bus, driver, log


def test_poll_period_below_one_raises() -> None:
    device = Device()
    with pytest.raises(ValueError):
        PollingDriver(Bus(device), device.run_until, poll_period_cycles=0)


def test_polling_latency_is_between_0_and_poll_period_plus_read_cost() -> None:
    period = 1_000
    _, _, _, log = run_polling(period, clicks=CLICKS, until=max(CLICKS) + 2 * period)
    latencies = [d - c for d, c in zip(log.detections, CLICKS, strict=True)]
    assert all(0 <= lat <= period + LATENCY for lat in latencies)
    # Spread over the period, not constant: when a click lands relative to the poll is luck.
    assert len(set(latencies)) > 10


def test_every_click_is_detected_once() -> None:
    _, _, _, log = run_polling(1_000, clicks=CLICKS, until=max(CLICKS) + 2_000)
    assert len(log.detections) == len(CLICKS)


def test_two_clicks_between_polls_are_both_detected_at_the_same_poll() -> None:
    _, _, _, log = run_polling(1_000, clicks=(5_100, 5_200), until=10_000)
    assert len(log.detections) == 2
    assert log.detections[0] == log.detections[1]


def test_every_poll_costs_two_reads_however_few_events() -> None:
    # 2 reads for the clock and 1 for the initial COUNT, then COUNT + LEVEL every poll.
    device, bus, driver, _ = run_polling(1_000, until=100_000)
    assert driver.polls == 100
    assert bus.read_count == 3 + 2 * driver.polls
    assert device.scope.edges == ()


def test_fast_polling_keeps_a_stream_on_time() -> None:
    # 20 commands consumed per 1_000-cycle poll, 64 queued: refill keeps well ahead.
    cmds = stream_of(300, 50)
    device, bus, _, log = run_polling(1_000, cmds, until=cmds[-1].timestamp_cycles)
    assert bus.read32(r.addr("SYSTEM.STATUS")) == 0
    assert log.pushed == 300
    assert [e.ideal_cycle for e in device.scope.edges] == [c.timestamp_cycles for c in cmds]
    assert all(e.offset == 0 for e in device.scope.edges)


def test_slow_polling_underruns() -> None:
    # 64 queued commands last 64 * 50 = 3_200 cycles, but the next poll is 5_000 later.
    cmds = stream_of(300, 50)
    _, bus, _, _ = run_polling(5_000, cmds, until=cmds[-1].timestamp_cycles)
    assert bus.read32(r.addr("SYSTEM.STATUS")) & r.STATUS_UNDERRUN
