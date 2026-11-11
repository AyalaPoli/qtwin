"""Event-driven sequencer, tested on its own with a real Clock and FIFO and recording callbacks."""

import pytest

from qtwin.device import registers as r
from qtwin.device.clock import Clock
from qtwin.device.fifo import CommandFifo
from qtwin.device.registers import Command
from qtwin.device.sequencer import Sequencer

GPIO0 = r.addr("GPIO0.LEVEL")


class Rig:
    """Sequencer plus logs of what it applied (cycle, command) and how often it flagged late."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.fifo = CommandFifo(capacity=8)
        self.applied: list[tuple[int, Command]] = []
        self.late_count = 0
        self.seq = Sequencer(self.fifo, self.clock, apply=self._apply, on_late=self._late)

    def _apply(self, cmd: Command) -> None:
        self.applied.append((self.clock.now, cmd))

    def _late(self) -> None:
        self.late_count += 1


def cmd(ts: int, value: int = 1) -> Command:
    return Command(timestamp_cycles=ts, target=GPIO0, value=value)


def test_fires_at_exactly_its_timestamp() -> None:
    rig = Rig()
    rig.fifo.push(cmd(1000))
    rig.seq.run_until(999)
    assert rig.applied == []
    rig.seq.run_until(1000)
    assert rig.applied == [(1000, cmd(1000))]


def test_clock_ends_at_the_requested_cycle() -> None:
    rig = Rig()
    rig.fifo.push(cmd(10))
    rig.seq.run_until(5000)
    assert rig.clock.now == 5000


def test_command_after_target_stays_queued() -> None:
    rig = Rig()
    rig.fifo.push(cmd(100))
    rig.fifo.push(cmd(200))
    rig.seq.run_until(150)
    assert [c.timestamp_cycles for _, c in rig.applied] == [100]
    assert len(rig.fifo) == 1


def test_same_timestamp_keeps_push_order() -> None:
    rig = Rig()
    rig.fifo.push(cmd(50, value=1))
    rig.fifo.push(cmd(50, value=2))
    rig.seq.run_until(50)
    assert rig.applied == [(50, cmd(50, 1)), (50, cmd(50, 2))]
    assert rig.late_count == 0


def test_past_timestamp_executes_now_and_flags_late() -> None:
    rig = Rig()
    rig.seq.run_until(500)
    rig.fifo.push(cmd(100))
    rig.seq.run_until(600)
    assert rig.applied == [(500, cmd(100))]
    assert rig.late_count == 1


def test_out_of_order_push_is_not_sorted_the_second_goes_late() -> None:
    rig = Rig()
    rig.fifo.push(cmd(300))
    rig.fifo.push(cmd(100))
    rig.seq.run_until(400)
    assert rig.applied == [(300, cmd(300)), (300, cmd(100))]
    assert rig.late_count == 1


def test_timestamp_equal_to_now_is_on_time() -> None:
    rig = Rig()
    rig.seq.run_until(70)
    rig.fifo.push(cmd(70))
    rig.seq.run_until(70)
    assert rig.applied == [(70, cmd(70))]
    assert rig.late_count == 0


def test_run_until_the_past_raises() -> None:
    rig = Rig()
    rig.seq.run_until(10)
    with pytest.raises(ValueError):
        rig.seq.run_until(9)
