"""The device: register storage, address decoding and access rules.

Two kinds of access:
- Bus side (read/write): what the host can do. Every access rule (RO, RW, W1C, WO) applies here.
- Device side (set_status, push_command, run_until): what the hardware itself does, or what a
  test does to drive it. It bypasses the bus rules, the same way logic inside an FPGA drives a
  status bit directly.

Timed commands: the host writes CMD_WORDS words to FIFO.CMD_PUSH; the last word commits the command
into the FIFO; the sequencer applies it at its timestamp and every output write goes to the Scope.
"""

from qtwin.device.clock import Clock
from qtwin.device.fifo import CommandFifo
from qtwin.device.registers import (
    BY_ADDRESS,
    CMD_WORDS,
    CTRL_RESET,
    CTRL_RUN,
    FIFO_CAPACITY,
    OUTPUT_TARGETS,
    REG_MASK,
    REGISTERS,
    STATUS_LATE,
    Access,
    BusError,
    Command,
    Register,
    addr,
    decode_command,
)
from qtwin.device.sequencer import Sequencer
from qtwin.scope import Scope

_CTRL = addr("SYSTEM.CTRL")
_STATUS = addr("SYSTEM.STATUS")
_IRQ_STATUS = addr("SYSTEM.IRQ_STATUS")
_IRQ_ENABLE = addr("SYSTEM.IRQ_ENABLE")
_CYCLE_LO = addr("CLOCK.CYCLE_LO")
_CYCLE_HI = addr("CLOCK.CYCLE_HI")
_FIFO_LEVEL = addr("FIFO.LEVEL")


class Device:
    """Register-level model of the control box. Starts in its reset state.

    scope: where output edges are recorded; a fresh Scope if not given.
    """

    def __init__(self, scope: Scope | None = None) -> None:
        self.clock = Clock()
        self.scope = scope if scope is not None else Scope()
        self._fifo = CommandFifo(FIFO_CAPACITY)
        self._sequencer = Sequencer(self._fifo, self.clock, apply=self._apply, on_late=self._late)
        # Current value of every register, keyed by absolute byte address.
        self._values: dict[int, int] = {}
        # CMD_PUSH words received so far for the command being assembled (< CMD_WORDS).
        self._frame: list[int] = []
        self.reset()

    def reset(self) -> None:
        """Return every register (incl. the CYCLE_HI shadow) to its reset value; zero the clock;
        empty the FIFO and discard any half-written CMD_PUSH frame.

        Same effect as power-on or the host writing CTRL.RESET. The Scope is not cleared: it is
        an external instrument watching the outputs, not part of the device.
        """
        self.clock.reset()
        self._values = {reg.address: reg.reset for reg in REGISTERS}
        self._fifo.clear()
        self._frame = []

    @property
    def irq_line(self) -> bool:
        """Level of the interrupt wire to the host: high while any enabled event is pending.

        Computed, not stored, so it can never disagree with IRQ_STATUS and IRQ_ENABLE.
        """
        return (self._values[_IRQ_STATUS] & self._values[_IRQ_ENABLE]) != 0

    @property
    def fifo_level(self) -> int:
        """Commands currently queued (what FIFO.LEVEL reads)."""
        return len(self._fifo)

    # --- Bus side ---

    def read(self, address: int) -> int:
        """Return the 32-bit value at byte `address`. Raises BusError for a bad address or WO."""
        reg = self._decode(address)
        if reg.access is Access.WO:
            raise BusError(f"{reg.name} is write-only")
        if address == _CYCLE_LO:
            # The 64-bit counter needs two 32-bit reads and keeps running in between. Sampling it
            # once here and parking the high half in CYCLE_HI (the shadow) makes the LO-then-HI
            # pair one snapshot. CYCLE_HI itself is a plain stored value: reading it never
            # re-samples, so reading HI first returns whatever the last LO read latched.
            now = self.clock.now
            self._values[_CYCLE_HI] = now >> 32
            return now & REG_MASK
        if address == _FIFO_LEVEL:
            # Computed from the FIFO itself, like irq_line, so it can never go stale.
            return len(self._fifo)
        return self._values[address]

    def write(self, address: int, value: int) -> None:
        """Write a 32-bit `value` to byte `address`, following the register's access type.

        Raises ValueError if value is outside 0..2^32-1 (no silent masking), BusError for a bad
        address or a write to an RO register. A CMD_PUSH write that completes a frame can also
        raise BusError (bad target) or FifoFullError.
        """
        if not 0 <= value <= REG_MASK:
            raise ValueError(f"value must fit in 32 bits, got {value}")
        reg = self._decode(address)
        if reg.access is Access.RO:
            raise BusError(f"{reg.name} is read-only")
        if address == _CTRL and value & CTRL_RESET:
            # Self-clearing: the reset restores CTRL to 0, so RESET reads back 0 and any RUN bit
            # in the same write is discarded. The device always comes out of reset stopped.
            self.reset()
        elif reg.access is Access.RW:
            self._values[address] = value
        elif reg.access is Access.W1C:
            # Clearing with a mask means the host can't accidentally clear a bit the device set
            # after the host last read the register: it only clears bits it names explicitly.
            self._values[address] &= ~value
        else:
            # WO: FIFO.CMD_PUSH, the only write-only register.
            self._push_word(value)

    # --- Device side ---

    def set_status(self, mask: int) -> None:
        """Hardware raises the event bits in `mask` in both STATUS and IRQ_STATUS.

        The bits are sticky: they stay set until the host clears them with a W1C write.
        """
        self._values[_STATUS] |= mask
        self._values[_IRQ_STATUS] |= mask

    def push_command(self, cmd: Command) -> None:
        """Queue a command directly, skipping CMD_PUSH framing (for tests).

        Same checks as a bus push: BusError for a non-output target, FifoFullError if full.
        """
        if cmd.target not in OUTPUT_TARGETS:
            raise BusError(f"command target {cmd.target:#x} is not an output register")
        self._fifo.push(cmd)

    def run_until(self, cycle: int) -> None:
        """Advance simulated time to absolute `cycle` (in cycles).

        While CTRL.RUN is set, every queued command due by then is applied at its timestamp.
        While stopped the clock still counts but nothing is applied [assumed], so the host can fill
        the FIFO before starting. Raises ValueError if `cycle` is before the current time.
        """
        if self._values[_CTRL] & CTRL_RUN:
            self._sequencer.run_until(cycle)
        elif cycle < self.clock.now:
            raise ValueError(f"cycle {cycle} is in the past (now={self.clock.now})")
        else:
            self.clock.advance(cycle - self.clock.now)

    # --- Internals ---

    def _push_word(self, word: int) -> None:
        self._frame.append(word)
        if len(self._frame) < CMD_WORDS:
            return
        words = self._frame
        # Start the next frame clean before checking this one, so a rejected command can't
        # leave stale words that would shift every later frame out of alignment.
        self._frame = []
        ts_lo, ts_hi, target, value = words
        self.push_command(decode_command((ts_lo, ts_hi, target, value)))

    def _apply(self, cmd: Command) -> None:
        # Called by the sequencer with the clock already at the execution cycle.
        self._values[cmd.target] = cmd.value
        self.scope.record(self.clock.now, BY_ADDRESS[cmd.target].name, cmd.value)

    def _late(self) -> None:
        self.set_status(STATUS_LATE)

    def _decode(self, address: int) -> Register:
        # Unaligned first: a 32-bit bus can't address a register starting mid-word.
        if address % 4 != 0:
            raise BusError(f"unaligned address {address:#x}")
        reg = BY_ADDRESS.get(address)
        if reg is None:
            raise BusError(f"unmapped address {address:#x}")
        return reg
