"""Bounded, strictly in-order command FIFO."""

import pytest

from qtwin.device import registers as r
from qtwin.device.fifo import CommandFifo
from qtwin.device.registers import Command

GPIO0 = r.addr("GPIO0.LEVEL")


def cmd(ts: int, value: int = 1) -> Command:
    return Command(timestamp_cycles=ts, target=GPIO0, value=value)


def test_pops_in_push_order_never_sorted() -> None:
    fifo = CommandFifo(capacity=4)
    for c in (cmd(300), cmd(100), cmd(200)):
        fifo.push(c)
    assert [fifo.pop().timestamp_cycles for _ in range(3)] == [300, 100, 200]


def test_peek_returns_head_without_removing_it() -> None:
    fifo = CommandFifo(capacity=2)
    fifo.push(cmd(5))
    assert fifo.peek() == cmd(5)
    assert len(fifo) == 1


def test_len_counts_up_and_down() -> None:
    fifo = CommandFifo(capacity=3)
    assert len(fifo) == 0
    fifo.push(cmd(1))
    fifo.push(cmd(2))
    assert len(fifo) == 2
    fifo.pop()
    assert len(fifo) == 1


def test_push_to_full_fifo_is_refused_and_keeps_contents() -> None:
    # M5: the FIFO reports a refused push instead of raising; the device decides the policy
    # (drop + OVERFLOW). Replaces M2's FifoFullError tests: the behaviour changed as planned.
    fifo = CommandFifo(capacity=2)
    assert fifo.push(cmd(1)) is True
    assert fifo.push(cmd(2)) is True
    assert fifo.full
    assert fifo.push(cmd(3)) is False
    assert len(fifo) == 2
    assert fifo.pop() == cmd(1)
    assert fifo.pop() == cmd(2)


def test_peek_or_pop_on_empty_raises() -> None:
    fifo = CommandFifo(capacity=1)
    with pytest.raises(IndexError):
        fifo.peek()
    with pytest.raises(IndexError):
        fifo.pop()


def test_clear_empties() -> None:
    fifo = CommandFifo(capacity=2)
    fifo.push(cmd(1))
    fifo.clear()
    assert len(fifo) == 0


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CommandFifo(capacity=0)
