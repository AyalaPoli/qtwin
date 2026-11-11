"""Event-driven sequencer: applies the FIFO head at exactly its timestamp.

Event-driven means the clock jumps straight from one command's timestamp to the next. Nothing
happens on the cycles in between, so there is no point looping over them one by one: a run of
10^9 cycles with 3 commands costs 3 iterations.
"""

from collections.abc import Callable

from qtwin.device.clock import Clock
from qtwin.device.fifo import CommandFifo
from qtwin.device.registers import Command


class Sequencer:
    """Pops commands from `fifo` in order and hands each one to `apply` at its cycle.

    apply: performs the output change; called with the clock already at the execution cycle.
    on_late: called once per command whose timestamp had already passed when it reached the head.
    """

    def __init__(
        self,
        fifo: CommandFifo,
        clock: Clock,
        apply: Callable[[Command], None],
        on_late: Callable[[], None],
    ) -> None:
        self._fifo = fifo
        self._clock = clock
        self._apply = apply
        self._on_late = on_late

    def run_until(self, cycle: int) -> None:
        """Advance time to `cycle` (absolute, in cycles), executing every head due by then.

        Raises ValueError if `cycle` is before the current time.
        """
        if cycle < self._clock.now:
            raise ValueError(f"cycle {cycle} is in the past (now={self._clock.now})")
        # Only the head is ever examined. A later-stamped command at the head blocks everything
        # behind it, exactly like a hardware FIFO; whatever was stamped earlier behind it goes late.
        while len(self._fifo) > 0 and self._fifo.peek().timestamp_cycles <= cycle:
            cmd = self._fifo.pop()
            if cmd.timestamp_cycles < self._clock.now:
                # Late: execute now rather than drop. A dropped "beam off" would leave the beam on,
                # while a late one only shifts it; the sticky flag makes the miss visible.
                self._on_late()
            else:
                self._clock.advance(cmd.timestamp_cycles - self._clock.now)
            self._apply(cmd)
        self._clock.advance(cycle - self._clock.now)
