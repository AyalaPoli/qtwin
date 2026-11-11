"""The device: register storage, address decoding and access rules.

Two kinds of access:
- Bus side (read/write): what the host can do. Every access rule (RO, RW, W1C, WO) applies here.
- Device side (set_status, push_command, run_until): what the hardware itself does, or what a
  test does to drive it. It bypasses the bus rules, the same way logic inside an FPGA drives a
  status bit directly.

Output timing: the sequencer applies a command at its ideal timestamp, then each lane's output path
adds a fixed skew, the SKEW_TRIM delay and accumulated drift before the edge appears, like a cable
and a delay line after the register.

Timed commands: the host writes CMD_WORDS words to FIFO.CMD_PUSH; the last word commits the command
into the FIFO; the sequencer applies it at its timestamp and every output write goes to the Scope.

Two event sources share the clock (M5): bus writes arriving at their arrival cycle, and sequencer
commands at their timestamp. run_until merges them in cycle order; on the same cycle the bus write
is applied first, so a frame completing on its own timestamp cycle is still on time [assumed].
"""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from qtwin.device.clock import Clock, drift_offset
from qtwin.device.fifo import CommandFifo
from qtwin.device.registers import (
    BY_ADDRESS,
    BY_NAME,
    CMD_WORDS,
    CTRL_RESET,
    CTRL_RUN,
    FIFO_CAPACITY,
    OUTPUT_TARGETS,
    REG_MASK,
    REGISTERS,
    STATUS_LATE,
    STATUS_OVERFLOW,
    Access,
    BusError,
    Command,
    Register,
    addr,
    decode_command,
)
from qtwin.device.sequencer import Sequencer
from qtwin.scope import LANES, Scope, component

_CTRL = addr("SYSTEM.CTRL")
_STATUS = addr("SYSTEM.STATUS")
_IRQ_STATUS = addr("SYSTEM.IRQ_STATUS")
_IRQ_ENABLE = addr("SYSTEM.IRQ_ENABLE")
_CYCLE_LO = addr("CLOCK.CYCLE_LO")
_CYCLE_HI = addr("CLOCK.CYCLE_HI")
_FIFO_LEVEL = addr("FIFO.LEVEL")
_FIFO_DROPPED = addr("FIFO.DROPPED")
# Lanes that have a SKEW_TRIM register (AOM and GPIO; SNSPD has none).
_SKEW_TRIM = {
    lane: BY_NAME[f"{lane}.SKEW_TRIM"].address for lane in LANES if f"{lane}.SKEW_TRIM" in BY_NAME
}


@dataclass(frozen=True)
class LaneTiming:
    """Timing faults of one lane's output path, fixed for a run.

    skew_cycles: constant delay in cycles (4 ns each), >= 0. Positive only because a cable or delay
        line can only delay: no output can change before it was commanded.
    drift_ppb: frequency error of the lane's timing in parts per billion, + or - (50_000 = +50 ppm).
    """

    skew_cycles: int = 0
    drift_ppb: int = 0

    def __post_init__(self) -> None:
        if self.skew_cycles < 0:
            raise ValueError(f"skew_cycles must be >= 0, got {self.skew_cycles}")


class Device:
    """Register-level model of the control box. Starts in its reset state.

    scope: where output edges are recorded; a fresh Scope if not given.
    timing: injected faults per lane name ("AOM1"); lanes not listed are ideal. A fault scenario
        sets this. It is not a register: the driver can't see it, only measure its effect.
    """

    def __init__(
        self, scope: Scope | None = None, timing: Mapping[str, LaneTiming] | None = None
    ) -> None:
        unknown = set(timing or {}) - set(LANES)
        if unknown:
            raise ValueError(f"unknown lanes in timing: {sorted(unknown)}")
        self._timing = dict(timing or {})
        self.clock = Clock()
        self.scope = scope if scope is not None else Scope()
        self._fifo = CommandFifo(FIFO_CAPACITY)
        self._sequencer = Sequencer(self._fifo, self.clock, apply=self._apply, on_late=self._late)
        # Current value of every register, keyed by absolute byte address.
        self._values: dict[int, int] = {}
        # CMD_PUSH words received so far for the command being assembled (< CMD_WORDS).
        self._frame: list[int] = []
        # Commands dropped on overflow since reset (what FIFO.DROPPED reads), 0..REG_MASK.
        self._dropped = 0
        # Bus writes in flight: (arrival cycle, address, value), arrival order = issue order.
        self._pending: deque[tuple[int, int, int]] = deque()
        self.reset()

    def reset(self) -> None:
        """Return every register (incl. the CYCLE_HI shadow) to its reset value; zero the clock;
        empty the FIFO, discard any half-written CMD_PUSH frame and bus writes still in flight
        [assumed: the bus port is reset like the rest], and zero the dropped counter.

        Same effect as power-on or the host writing CTRL.RESET. The Scope is not cleared: it is
        an external instrument watching the outputs, not part of the device.
        """
        self.clock.reset()
        self._values = {reg.address: reg.reset for reg in REGISTERS}
        self._fifo.clear()
        self._frame = []
        self._dropped = 0
        self._pending.clear()

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
        if address == _FIFO_DROPPED:
            return self._dropped
        return self._values[address]

    def write(self, address: int, value: int) -> None:
        """Write a 32-bit `value` to byte `address`, following the register's access type.

        Raises ValueError if value is outside 0..2^32-1 (no silent masking), BusError for a bad
        address or a write to an RO register. A CMD_PUSH write that completes a frame can also
        raise BusError (bad target); a full FIFO drops the command (see push_command).
        """
        reg = self.check_write(address, value)
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

    def check_write(self, address: int, value: int) -> Register:
        """Raise exactly as write() would for a bad value, address or RO register; change nothing.

        The bus calls it at issue time, so a bad write fails at the host's call, not on arrival.
        """
        if not 0 <= value <= REG_MASK:
            raise ValueError(f"value must fit in 32 bits, got {value}")
        reg = self._decode(address)
        if reg.access is Access.RO:
            raise BusError(f"{reg.name} is read-only")
        return reg

    def schedule_write(self, arrival_cycle: int, address: int, value: int) -> int:
        """Bus port: apply write(address, value) at `arrival_cycle` (absolute cycles).

        Returns the cycle it will actually land: never before an earlier write still in flight,
        because the bus delivers in issue order [assumed: like AXI writes with one ID]. If that is
        the current cycle the write is applied now. Raises ValueError for a cycle in the past.
        """
        if arrival_cycle < self.clock.now:
            raise ValueError(f"arrival {arrival_cycle} is in the past (now={self.clock.now})")
        if self._pending:
            arrival_cycle = max(arrival_cycle, self._pending[-1][0])
        if arrival_cycle == self.clock.now:
            # Nothing can be pending here: every pending write lands after now, and this one
            # would have been held behind it. So applying now keeps the order.
            self.write(address, value)
        else:
            self._pending.append((arrival_cycle, address, value))
        return arrival_cycle

    # --- Device side ---

    def set_status(self, mask: int) -> None:
        """Hardware raises the event bits in `mask` in both STATUS and IRQ_STATUS.

        The bits are sticky: they stay set until the host clears them with a W1C write.
        """
        self._values[_STATUS] |= mask
        self._values[_IRQ_STATUS] |= mask

    def push_command(self, cmd: Command) -> None:
        """Queue a command, skipping CMD_PUSH framing (tests); a completed bus frame ends here too.

        Raises BusError for a non-output target. If the FIFO is full the command is dropped:
        STATUS.OVERFLOW (and IRQ) is set and FIFO.DROPPED counts it. Queued commands are untouched.
        """
        if cmd.target not in OUTPUT_TARGETS:
            raise BusError(f"command target {cmd.target:#x} is not an output register")
        if not self._fifo.push(cmd):
            # Tail drop: the commands already queued were accepted first and keep their slots.
            # Saturate instead of wrapping, so a huge count can never read back as a small one.
            self._dropped = min(self._dropped + 1, REG_MASK)
            self.set_status(STATUS_OVERFLOW)

    def run_until(self, cycle: int) -> None:
        """Advance simulated time to absolute `cycle` (in cycles).

        Bus writes in flight land at their arrival cycle, before the sequencer on that cycle.
        While CTRL.RUN is set, every queued command due by then is applied at its timestamp.
        While stopped the clock still counts but nothing is applied [assumed], so the host can fill
        the FIFO before starting. Raises ValueError if `cycle` is before the current time.
        """
        if cycle < self.clock.now:
            raise ValueError(f"cycle {cycle} is in the past (now={self.clock.now})")
        while self._pending and self._pending[0][0] <= cycle:
            arrival, address, value = self._pending.popleft()
            # Sequencer up to the cycle before, then the write at its cycle. Several writes can
            # land on one cycle; only the first moves time, and all of them still go before the
            # sequencer's turn on that cycle. The write may be CTRL.RUN, so the running state is
            # re-read for each stretch.
            if arrival > self.clock.now:
                self._advance(arrival - 1)
                self.clock.advance(1)
            self.write(address, value)
        self._advance(cycle)

    # --- Internals ---

    def _advance(self, cycle: int) -> None:
        # Time passes up to `cycle` (>= now, checked by run_until) with no bus write landing;
        # the sequencer runs only while RUN.
        if self._values[_CTRL] & CTRL_RUN:
            self._sequencer.run_until(cycle)
        else:
            self.clock.advance(cycle - self.clock.now)

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
        # Called by the sequencer with the clock already at the execution cycle. For a late
        # command that is after its timestamp, and the Scope keeps both so the miss is measurable.
        # The register changes now; the output path delay is added on top for the Scope edge.
        self._values[cmd.target] = cmd.value
        channel = BY_ADDRESS[cmd.target].name
        self.scope.record(
            channel=channel,
            ideal_cycle=cmd.timestamp_cycles,
            actual_cycle=self.clock.now + self._output_delay(component(channel)),
            value=cmd.value,
        )

    def _output_delay(self, lane: str) -> int:
        # Cycles from the register change to the output edge. Drift is evaluated at the current
        # cycle (cycles since reset): the longer the run, the more the lane's timing has slipped.
        # Never negative: skew and trim are >= 0, and |drift| < now while |drift_ppb| < 10^9.
        timing = self._timing.get(lane, LaneTiming())
        trim = self._values[_SKEW_TRIM[lane]] if lane in _SKEW_TRIM else 0
        return timing.skew_cycles + trim + drift_offset(self.clock.now, timing.drift_ppb)

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
