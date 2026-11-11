"""Bus timing (M5): named latency, seeded jitter, in-order delivery as device events."""

import pytest

from qtwin.bus import Bus, BusWrite
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import BusError, Command

CTRL = r.addr("SYSTEM.CTRL")
STATUS = r.addr("SYSTEM.STATUS")
CMD_PUSH = r.addr("FIFO.CMD_PUSH")
AMPL = r.addr("AOM0.AMPL")
GPIO0 = r.addr("GPIO0.LEVEL")


def push(bus: Bus, cmd: Command) -> None:
    for word in r.encode_command(cmd):
        bus.write32(CMD_PUSH, word)


def jitters(seed: int, n: int = 50, max_jitter: int = 30) -> list[int]:
    bus = Bus(Device(), max_jitter_cycles=max_jitter, seed=seed)
    for i in range(n):
        bus.write32(AMPL, i)
    return [w.jitter_cycles for w in bus.writes]


# --- Defaults: the M1-M6 instant bus ---


def test_default_bus_is_instant() -> None:
    device = Device()
    bus = Bus(device)
    bus.write32(AMPL, 5)
    assert bus.read32(AMPL) == 5
    assert bus.writes == (BusWrite(0, 0, 0, AMPL, 5),)


@pytest.mark.parametrize("kwargs", [{"bus_latency_cycles": -1}, {"max_jitter_cycles": -1}])
def test_negative_timing_parameters_raise(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Bus(Device(), **kwargs)


# --- Latency ---


def test_write_lands_exactly_bus_latency_cycles_later() -> None:
    device = Device()
    bus = Bus(device, bus_latency_cycles=10)
    device.run_until(100)
    bus.write32(AMPL, 7)
    # Device-side reads: a bus read would itself take 10 cycles (M7) and sample at 119.
    device.run_until(109)
    assert device.read(AMPL) == 0
    device.run_until(110)
    assert device.read(AMPL) == 7
    assert bus.writes == (BusWrite(100, 110, 0, AMPL, 7),)


def test_bad_access_raises_at_issue_not_at_arrival() -> None:
    # The host must hear about a bad address when it makes the call, not cycles later inside
    # whatever happens to advance time.
    bus = Bus(Device(), bus_latency_cycles=10)
    with pytest.raises(BusError):
        bus.write32(r.addr("SYSTEM.ID"), 1)
    with pytest.raises(ValueError):
        bus.write32(AMPL, 2**32)
    assert bus.writes == ()


def test_reset_discards_writes_still_in_flight() -> None:
    # [assumed] CTRL.RESET also resets the device's bus port, like every other piece of state.
    device = Device()
    bus = Bus(device, bus_latency_cycles=10)
    bus.write32(AMPL, 7)
    device.reset()
    device.run_until(50)
    assert bus.read32(AMPL) == 0


# --- Interleaving with the sequencer ---


@pytest.mark.parametrize(("ts", "late"), [(10, False), (9, True)])
def test_arrival_is_applied_before_the_sequencer_on_the_same_cycle(ts: int, late: bool) -> None:
    # All 4 words issued at cycle 0 arrive at 10. A command stamped 10 is on time; 9 is late.
    device = Device()
    bus = Bus(device, bus_latency_cycles=10)
    device.run_until(0)
    device.write(CTRL, r.CTRL_RUN)
    push(bus, Command(ts, GPIO0, 1))
    device.run_until(20)
    assert device.scope.edges[0].actual_cycle == 10
    assert bool(bus.read32(STATUS) & r.STATUS_LATE) is late


def test_delayed_ctrl_run_starts_the_sequencer_on_arrival() -> None:
    device = Device()
    device.push_command(Command(5, GPIO0, 1))
    bus = Bus(device, bus_latency_cycles=8)
    bus.write32(CTRL, r.CTRL_RUN)
    device.run_until(20)
    # Due at 5 but RUN only arrived at 8: runs late at 8.
    assert device.scope.edges[0].actual_cycle == 8


# --- Jitter ---


def test_jitter_stays_within_0_and_max() -> None:
    draws = jitters(seed=1, n=500)
    assert min(draws) == 0
    assert max(draws) == 30


def test_same_seed_gives_same_jitter_sequence() -> None:
    assert jitters(seed=1) == jitters(seed=1)


def test_different_seed_gives_different_jitter_sequence() -> None:
    assert jitters(seed=1) != jitters(seed=2)


def test_arrival_is_issue_plus_latency_plus_jitter_unless_held_back() -> None:
    bus = Bus(Device(), bus_latency_cycles=20, max_jitter_cycles=30, seed=1)
    for i in range(50):
        bus.write32(AMPL, i)
    previous = 0
    for w in bus.writes:
        assert w.arrival_cycle == max(w.issue_cycle + 20 + w.jitter_cycles, previous)
        previous = w.arrival_cycle


def test_jitter_never_reorders_writes() -> None:
    device = Device()
    bus = Bus(device, bus_latency_cycles=5, max_jitter_cycles=30, seed=3)
    for i in range(100):
        bus.write32(AMPL, i)
        device.run_until(device.clock.now + 1)
    arrivals = [w.arrival_cycle for w in bus.writes]
    assert arrivals == sorted(arrivals)
    device.run_until(1_000)
    assert bus.read32(AMPL) == 99


def test_cmd_push_frames_stay_aligned_under_jitter() -> None:
    # 4 words per command: one reordered word would corrupt every later frame.
    device = Device()
    bus = Bus(device, bus_latency_cycles=5, max_jitter_cycles=30, seed=4)
    cmds = [Command(1_000 + 10 * i, GPIO0, i % 2) for i in range(20)]
    for cmd in cmds:
        for word in r.encode_command(cmd):
            bus.write32(CMD_PUSH, word)
            device.run_until(device.clock.now + 1)
    device.write(CTRL, r.CTRL_RUN)
    device.run_until(2_000)
    got = [(e.ideal_cycle, e.value) for e in device.scope.edges]
    assert got == [(c.timestamp_cycles, c.value) for c in cmds]


# --- Read cost (M7) ---


def test_read_takes_bus_latency_cycles_and_samples_on_arrival() -> None:
    device = Device()
    bus = Bus(device, bus_latency_cycles=10)
    device.run_until(100)
    lo = bus.read32(r.addr("CLOCK.CYCLE_LO"))
    assert lo == 110  # sampled when the read reached the device
    assert device.clock.now == 110  # the host waited for it


def test_read_sees_a_write_that_lands_during_the_read() -> None:
    device = Device()
    bus = Bus(device, bus_latency_cycles=10)
    device.run_until(100)
    bus.write32(AMPL, 7)  # lands at 110
    device.run_until(105)
    assert bus.read32(AMPL) == 7  # sampled at 115


def test_reads_are_counted() -> None:
    bus = Bus(Device(), bus_latency_cycles=3)
    assert bus.read_count == 0
    for _ in range(5):
        bus.read32(STATUS)
    assert bus.read_count == 5


def test_reads_draw_no_jitter() -> None:
    # Interleaving reads must not change the write jitter sequence of a seed.
    bus = Bus(Device(), bus_latency_cycles=2, max_jitter_cycles=30, seed=4)
    for i in range(50):
        bus.read32(STATUS)
        bus.write32(AMPL, i)
    assert [w.jitter_cycles for w in bus.writes] == jitters(4)


def test_instant_bus_read_takes_no_time() -> None:
    device = Device()
    bus = Bus(device)
    device.run_until(50)
    bus.read32(STATUS)
    assert device.clock.now == 50
