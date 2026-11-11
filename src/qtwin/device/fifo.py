"""Bounded command FIFO between the bus (CMD_PUSH) and the sequencer.

Strictly first-in first-out, like a hardware FIFO: it never sorts. The sequencer only ever looks at
the head, so commands with the same timestamp come out in push order without any extra rule.
"""

from collections import deque

from qtwin.device.registers import BusError, Command


class FifoFullError(BusError):
    """CMD_PUSH committed a command while the FIFO was full.

    M2 behaviour (strict, [assumed]): the push is rejected with an error. M5 replaces this with
    the hardware policy: drop the command, set STATUS.OVERFLOW and count the drop.
    """


class CommandFifo:
    """Holds up to `capacity` commands (count of commands, not bytes)."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self.capacity = capacity
        # No maxlen: a deque with maxlen silently discards the oldest item, and a full FIFO
        # must never lose a command without saying so.
        self._items: deque[Command] = deque()

    def __len__(self) -> int:
        return len(self._items)

    def push(self, cmd: Command) -> None:
        """Append at the tail. Raises FifoFullError if `capacity` commands are already queued."""
        if len(self._items) >= self.capacity:
            raise FifoFullError(f"FIFO full ({self.capacity} commands)")
        self._items.append(cmd)

    def peek(self) -> Command:
        """Return the head without removing it. Raises IndexError if empty."""
        return self._items[0]

    def pop(self) -> Command:
        """Remove and return the head. Raises IndexError if empty."""
        return self._items.popleft()

    def clear(self) -> None:
        """Drop every queued command (CTRL.RESET)."""
        self._items.clear()
