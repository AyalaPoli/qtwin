"""Timed commands through the whole device: CMD_PUSH framing, FIFO.LEVEL, RUN, late flag, Scope."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import BusError, Command
from qtwin.scope import Edge, Scope

CTRL = r.addr("SYSTEM.CTRL")
STATUS = r.addr("SYSTEM.STATUS")
IRQ_STATUS = r.addr("SYSTEM.IRQ_STATUS")
CMD_PUSH = r.addr("FIFO.CMD_PUSH")
LEVEL = r.addr("FIFO.LEVEL")
GPIO0 = r.addr("GPIO0.LEVEL")
AOM1_AMPL = r.addr("AOM1.AMPL")


@pytest.fixture
def device() -> Device:
    return Device()


@pytest.fixture
def bus(device: Device) -> Bus:
    return Bus(device)


def push_over_bus(bus: Bus, cmd: Command) -> None:
    for word in r.encode_command(cmd):
        bus.write32(CMD_PUSH, word)


def start(bus: Bus) -> None:
    bus.write32(CTRL, r.CTRL_RUN)


# --- Framing ---


def test_partial_frame_does_not_enter_the_fifo(bus: Bus) -> None:
    words = r.encode_command(Command(100, GPIO0, 1))
    for word in words[:3]:
        bus.write32(CMD_PUSH, word)
    assert bus.read32(LEVEL) == 0
    bus.write32(CMD_PUSH, words[3])
    assert bus.read32(LEVEL) == 1


def test_frame_over_bus_fires_at_its_timestamp(device: Device, bus: Bus) -> None:
    push_over_bus(bus, Command(2**32 + 5, GPIO0, 1))
    start(bus)
    device.run_until(2**32 + 5)
    assert device.scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=2**32 + 5, actual_cycle=2**32 + 5, value=1),
    )


def test_reset_mid_frame_discards_the_partial_command(device: Device, bus: Bus) -> None:
    bus.write32(CMD_PUSH, 999)
    bus.write32(CMD_PUSH, 0)
    bus.write32(CTRL, r.CTRL_RESET)
    push_over_bus(bus, Command(10, GPIO0, 1))
    start(bus)
    device.run_until(10)
    assert device.scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=10, actual_cycle=10, value=1),
    )


def test_reset_empties_the_fifo(device: Device, bus: Bus) -> None:
    device.push_command(Command(10, GPIO0, 1))
    bus.write32(CTRL, r.CTRL_RESET)
    assert bus.read32(LEVEL) == 0


@pytest.mark.parametrize("name", ["AOM0.SKEW_TRIM", "SYSTEM.CTRL", "FIFO.CMD_PUSH"])
def test_non_output_target_raises_on_commit(bus: Bus, name: str) -> None:
    with pytest.raises(BusError):
        push_over_bus(bus, Command(10, r.addr(name), 1))
    assert bus.read32(LEVEL) == 0


def test_bad_frame_does_not_corrupt_the_next_one(device: Device, bus: Bus) -> None:
    with pytest.raises(BusError):
        push_over_bus(bus, Command(10, r.addr("SYSTEM.CTRL"), 1))
    push_over_bus(bus, Command(20, GPIO0, 1))
    assert device.fifo_level == 1


def test_device_side_push_checks_target_too(device: Device) -> None:
    with pytest.raises(BusError):
        device.push_command(Command(10, r.addr("SYSTEM.CTRL"), 1))


# --- FIFO.LEVEL ---


def test_level_counts_up_on_push_and_down_as_commands_fire(device: Device, bus: Bus) -> None:
    for ts in (100, 200, 300):
        device.push_command(Command(ts, GPIO0, 1))
    assert bus.read32(LEVEL) == 3
    start(bus)
    device.run_until(200)
    assert bus.read32(LEVEL) == 1
    device.run_until(300)
    assert bus.read32(LEVEL) == 0


# --- Overflow (M5 replaces M2's FifoFullError: drop is the hardware policy planned in M2) ---

DROPPED = r.addr("FIFO.DROPPED")


def fill(device: Device) -> None:
    for ts in range(r.FIFO_CAPACITY):
        device.push_command(Command(ts, GPIO0, 1))


def test_full_fifo_drops_the_new_command(device: Device, bus: Bus) -> None:
    fill(device)
    device.push_command(Command(999, GPIO0, 1))
    assert bus.read32(LEVEL) == r.FIFO_CAPACITY
    assert bus.read32(DROPPED) == 1
    start(bus)
    device.run_until(1_000)
    # The 64 queued commands all ran; the dropped one (ts 999) never did.
    assert [e.ideal_cycle for e in device.scope.edges] == list(range(r.FIFO_CAPACITY))


def test_overflow_over_bus_sets_sticky_status_and_irq(device: Device, bus: Bus) -> None:
    fill(device)
    push_over_bus(bus, Command(999, GPIO0, 1))
    push_over_bus(bus, Command(1_000, GPIO0, 0))
    assert bus.read32(STATUS) == r.STATUS_OVERFLOW
    assert bus.read32(IRQ_STATUS) == r.IRQ_OVERFLOW
    assert bus.read32(DROPPED) == 2


def test_overflow_frame_does_not_misalign_the_next_one(device: Device, bus: Bus) -> None:
    fill(device)
    push_over_bus(bus, Command(999, GPIO0, 1))
    start(bus)
    device.run_until(100)  # drains the FIFO
    push_over_bus(bus, Command(500, AOM1_AMPL, 7))
    device.run_until(500)
    assert device.scope.edges[-1] == Edge("AOM1.AMPL", ideal_cycle=500, actual_cycle=500, value=7)


def test_dropped_counter_is_cleared_by_reset(device: Device, bus: Bus) -> None:
    fill(device)
    device.push_command(Command(999, GPIO0, 1))
    bus.write32(CTRL, r.CTRL_RESET)
    assert bus.read32(DROPPED) == 0


def test_dropped_counter_saturates_at_32_bits(device: Device, bus: Bus) -> None:
    # 2^32 drops can't be produced in a test; start the counter just below its maximum.
    device._dropped = r.REG_MASK - 1
    fill(device)
    device.push_command(Command(999, GPIO0, 1))
    device.push_command(Command(999, GPIO0, 1))
    assert bus.read32(DROPPED) == r.REG_MASK


# --- Underrun (M5): a command committed into an empty FIFO after its timestamp, while running ---

ERRORS = r.STATUS_UNDERRUN | r.STATUS_OVERFLOW | r.STATUS_LATE


def running_at(device: Device, bus: Bus, cycle: int) -> None:
    start(bus)
    device.run_until(cycle)


def test_late_commit_into_empty_fifo_sets_underrun_and_late(device: Device, bus: Bus) -> None:
    running_at(device, bus, 500)
    device.push_command(Command(400, GPIO0, 1))
    # Flagged on the commit cycle itself, before any more time passes.
    assert bus.read32(STATUS) & ERRORS == r.STATUS_UNDERRUN
    assert bus.read32(IRQ_STATUS) & r.IRQ_UNDERRUN
    device.run_until(500)
    assert bus.read32(STATUS) & ERRORS == r.STATUS_UNDERRUN | r.STATUS_LATE
    assert device.scope.edges[0].actual_cycle == 500


def test_commit_on_its_own_timestamp_is_not_an_underrun(device: Device, bus: Bus) -> None:
    running_at(device, bus, 500)
    device.push_command(Command(500, GPIO0, 1))
    device.run_until(500)
    assert bus.read32(STATUS) & ERRORS == 0


def test_late_commit_behind_queued_commands_is_late_only(device: Device, bus: Bus) -> None:
    # The FIFO is not empty, so the sequencer was not starved: this is head-of-line blocking.
    device.push_command(Command(1_000, GPIO0, 1))
    running_at(device, bus, 500)
    device.push_command(Command(400, GPIO0, 0))
    device.run_until(1_000)
    assert bus.read32(STATUS) & ERRORS == r.STATUS_LATE


def test_late_commit_while_stopped_is_not_an_underrun(device: Device, bus: Bus) -> None:
    # A stopped sequencer isn't consuming anything, so it can't be starved [assumed].
    device.run_until(500)
    device.push_command(Command(400, GPIO0, 1))
    assert bus.read32(STATUS) & r.STATUS_UNDERRUN == 0


def test_fifo_empty_after_the_last_command_is_not_an_underrun(device: Device, bus: Bus) -> None:
    for ts in (100, 200):
        device.push_command(Command(ts, GPIO0, ts // 100 % 2))
    running_at(device, bus, 1_000_000)
    assert bus.read32(STATUS) & ERRORS == 0


def test_underrun_over_a_slow_bus_is_flagged_on_the_arrival_cycle(device: Device) -> None:
    bus = Bus(device, bus_latency_cycles=50)
    device.write(CTRL, r.CTRL_RUN)
    push_over_bus(bus, Command(30, GPIO0, 1))  # all 4 words land at cycle 50
    # Device-side reads: a bus read would itself take 50 cycles (M7) and sample at 99.
    device.run_until(49)
    assert device.read(STATUS) & r.STATUS_UNDERRUN == 0
    device.run_until(50)
    assert device.read(STATUS) & ERRORS == r.STATUS_UNDERRUN | r.STATUS_LATE


# --- Instrumentation (M5): FIFO level and status events over time, for plots and tests ---


def test_fifo_levels_record_every_change_with_its_cycle(device: Device, bus: Bus) -> None:
    for ts in (100, 200, 300):
        device.push_command(Command(ts, GPIO0, 1))
    start(bus)
    device.run_until(1_000)
    assert device.fifo_levels == ((0, 1), (0, 2), (0, 3), (100, 2), (200, 1), (300, 0))


def test_a_dropped_command_does_not_change_the_level_trace(device: Device) -> None:
    fill(device)
    before = device.fifo_levels
    device.push_command(Command(999, GPIO0, 1))
    assert device.fifo_levels == before


def test_status_events_record_each_flag_at_its_exact_cycle(device: Device, bus: Bus) -> None:
    running_at(device, bus, 500)
    device.push_command(Command(400, GPIO0, 1))
    device.run_until(600)
    assert device.status_events == ((500, r.STATUS_UNDERRUN), (500, r.STATUS_LATE))


# --- Execution ---


def test_command_writes_the_register_at_its_cycle(device: Device, bus: Bus) -> None:
    device.push_command(Command(1000, AOM1_AMPL, 42))
    start(bus)
    device.run_until(999)
    assert bus.read32(AOM1_AMPL) == 0
    assert device.scope.edges == ()
    device.run_until(1000)
    assert bus.read32(AOM1_AMPL) == 42
    assert device.scope.edges == (
        Edge(channel="AOM1.AMPL", ideal_cycle=1000, actual_cycle=1000, value=42),
    )
    assert device.clock.now == 1000


def test_ties_keep_fifo_order(device: Device, bus: Bus) -> None:
    device.push_command(Command(50, GPIO0, 1))
    device.push_command(Command(50, GPIO0, 0))
    start(bus)
    device.run_until(50)
    assert device.scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=50, actual_cycle=50, value=1),
        Edge(channel="GPIO0.LEVEL", ideal_cycle=50, actual_cycle=50, value=0),
    )
    assert bus.read32(GPIO0) == 0


def test_nothing_fires_while_stopped_but_the_clock_runs(device: Device, bus: Bus) -> None:
    device.push_command(Command(10, GPIO0, 1))
    device.run_until(100)
    assert device.scope.edges == ()
    assert device.clock.now == 100
    assert bus.read32(LEVEL) == 1


def test_command_due_while_stopped_runs_late_after_start(device: Device, bus: Bus) -> None:
    device.push_command(Command(10, GPIO0, 1))
    device.run_until(100)
    start(bus)
    device.run_until(100)
    assert device.scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=10, actual_cycle=100, value=1),
    )
    assert bus.read32(STATUS) & r.STATUS_LATE


def test_past_timestamp_sets_late_in_status_and_irq_status(device: Device, bus: Bus) -> None:
    start(bus)
    device.run_until(500)
    device.push_command(Command(100, GPIO0, 1))
    device.run_until(500)
    assert device.scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=100, actual_cycle=500, value=1),
    )
    # Committed late into an empty running FIFO, so since M5 this is also an underrun.
    assert bus.read32(STATUS) == r.STATUS_LATE | r.STATUS_UNDERRUN
    assert bus.read32(IRQ_STATUS) == r.IRQ_LATE | r.IRQ_UNDERRUN


def test_on_time_commands_leave_status_clear(device: Device, bus: Bus) -> None:
    device.push_command(Command(5, GPIO0, 1))
    start(bus)
    device.run_until(10)
    assert bus.read32(STATUS) == 0


def test_same_input_gives_identical_trace() -> None:
    cmds = [Command(t, a, v) for t, a, v in [(10, GPIO0, 1), (10, AOM1_AMPL, 7), (40, GPIO0, 0)]]

    def run() -> tuple[Edge, ...]:
        device = Device()
        bus = Bus(device)
        for cmd in cmds:
            push_over_bus(bus, cmd)
        start(bus)
        device.run_until(100)
        return device.scope.edges

    assert run() == run()


def test_device_uses_the_scope_it_is_given() -> None:
    scope = Scope()
    device = Device(scope=scope)
    device.push_command(Command(0, GPIO0, 1))
    Bus(device).write32(CTRL, r.CTRL_RUN)
    device.run_until(0)
    assert scope.edges == (Edge(channel="GPIO0.LEVEL", ideal_cycle=0, actual_cycle=0, value=1),)
