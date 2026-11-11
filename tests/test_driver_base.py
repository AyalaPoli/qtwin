"""Driver helpers (M7): push_frame (one word per cycle) and unwrap_ts (LAST_TS wrap)."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.driver.base import push_frame, unwrap_ts

GPIO0 = r.addr("GPIO0.LEVEL")
WRAP = 2**32


# --- push_frame ---


def test_push_frame_writes_one_word_per_cycle_from_start() -> None:
    device = Device()
    bus = Bus(device, bus_latency_cycles=5)
    cmd = r.Command(1_000, GPIO0, 1)
    push_frame(bus, device.run_until, cmd, start=200)
    assert [w.issue_cycle for w in bus.writes] == [200, 201, 202, 203]
    assert [w.value for w in bus.writes] == list(r.encode_command(cmd))
    device.run_until(208)
    assert device.fifo_level == 1


# --- unwrap_ts: full click cycle from 32-bit LAST_TS and a later 64-bit time ---


@pytest.mark.parametrize(
    ("last_ts", "t_ref", "expected"),
    [
        (900, 1_000, 900),  # no wrap
        (1_000, 1_000, 1_000),  # click on the reference cycle
        (3, WRAP + 10, WRAP + 3),  # both after the wrap
        (WRAP - 5, WRAP + 10, WRAP - 5),  # click just before the wrap, reference just after
        (7, 5 * WRAP + 9, 5 * WRAP + 7),  # far into the 64-bit range
        (11, WRAP + 10, 11),  # 2^32 - 1 cycles before: the oldest click it can tell apart
    ],
)
def test_unwrap_ts(last_ts: int, t_ref: int, expected: int) -> None:
    assert unwrap_ts(last_ts, t_ref) == expected


def test_unwrap_ts_is_ambiguous_at_exactly_2_32_cycles() -> None:
    # A click 2^32 cycles before t_ref has the same LAST_TS as one on t_ref. Documented limit.
    assert unwrap_ts(10, WRAP + 10) == WRAP + 10


@pytest.mark.parametrize("last_ts", [-1, WRAP])
def test_unwrap_ts_rejects_values_outside_32_bits(last_ts: int) -> None:
    with pytest.raises(ValueError):
        unwrap_ts(last_ts, WRAP)


def test_unwrap_ts_recovers_a_device_click_across_the_wrap() -> None:
    click = WRAP - 5
    device = Device(clicks={"SNSPD0": (click,)})
    device.run_until(WRAP + 20)
    last_ts = Bus(device).read32(r.addr("SNSPD0.LAST_TS"))
    assert last_ts == WRAP - 5
    assert unwrap_ts(last_ts, device.clock.now) == click
