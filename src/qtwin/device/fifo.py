"""Bounded command FIFO between the bus (CMD_PUSH) and the sequencer.

Strictly first-in first-out, like a hardware FIFO: it never sorts. The sequencer only ever looks at
the head, so commands with the same timestamp come out in push order without any extra rule.

A push into a full FIFO is refused, not raised: like a hardware FIFO's "full" flag, it only
reports. What happens to the refused command (drop + STATUS.OVERFLOW) is the device's policy.
"""

from collections import deque

from qtwin.device.registers import Command


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

    @property
    def full(self) -> bool:
        """True when `capacity` commands are queued."""
        return len(self._items) >= self.capacity

    def push(self, cmd: Command) -> bool:
        """Append at the tail. Returns False (and changes nothing) if the FIFO is full."""
        if self.full:
            return False
        self._items.append(cmd)
        return True

    def peek(self) -> Command:
        """Return the head without removing it. Raises IndexError if empty."""
        return self._items[0]

    def pop(self) -> Command:
        """Remove and return the head. Raises IndexError if empty."""
        return self._items.popleft()

    def clear(self) -> None:
        """Drop every queued command (CTRL.RESET)."""
        self._items.clear()
