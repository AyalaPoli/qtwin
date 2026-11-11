"""Timed commands through the whole device: CMD_PUSH framing, FIFO.LEVEL, RUN, late flag, Scope."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.fifo import FifoFullError
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
    assert device.scope.edges == (Edge(2**32 + 5, "GPIO0.LEVEL", 1),)


def test_reset_mid_frame_discards_the_partial_command(device: Device, bus: Bus) -> None:
    bus.write32(CMD_PUSH, 999)
    bus.write32(CMD_PUSH, 0)
    bus.write32(CTRL, r.CTRL_RESET)
    push_over_bus(bus, Command(10, GPIO0, 1))
    start(bus)
    device.run_until(10)
    assert device.scope.edges == (Edge(10, "GPIO0.LEVEL", 1),)


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


def test_full_fifo_raises(device: Device) -> None:
    for ts in range(r.FIFO_CAPACITY):
        device.push_command(Command(ts, GPIO0, 1))
    with pytest.raises(FifoFullError):
        device.push_command(Command(999, GPIO0, 1))


# --- Execution ---


def test_command_writes_the_register_at_its_cycle(device: Device, bus: Bus) -> None:
    device.push_command(Command(1000, AOM1_AMPL, 42))
    start(bus)
    device.run_until(999)
    assert bus.read32(AOM1_AMPL) == 0
    assert device.scope.edges == ()
    device.run_until(1000)
    assert bus.read32(AOM1_AMPL) == 42
    assert device.scope.edges == (Edge(1000, "AOM1.AMPL", 42),)
    assert device.clock.now == 1000


def test_ties_keep_fifo_order(device: Device, bus: Bus) -> None:
    device.push_command(Command(50, GPIO0, 1))
    device.push_command(Command(50, GPIO0, 0))
    start(bus)
    device.run_until(50)
    assert device.scope.edges == (Edge(50, "GPIO0.LEVEL", 1), Edge(50, "GPIO0.LEVEL", 0))
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
    assert device.scope.edges == (Edge(100, "GPIO0.LEVEL", 1),)
    assert bus.read32(STATUS) & r.STATUS_LATE


def test_past_timestamp_sets_late_in_status_and_irq_status(device: Device, bus: Bus) -> None:
    start(bus)
    device.run_until(500)
    device.push_command(Command(100, GPIO0, 1))
    device.run_until(500)
    assert device.scope.edges == (Edge(500, "GPIO0.LEVEL", 1),)
    assert bus.read32(STATUS) == r.STATUS_LATE
    assert bus.read32(IRQ_STATUS) == r.IRQ_LATE


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
    assert scope.edges == (Edge(0, "GPIO0.LEVEL", 1),)
