"""Interrupt driver: the device tells the host when something happened; the host does nothing else.

The device raises its IRQ line when an enabled IRQ_STATUS bit is set: FIFO_LOW (the FIFO fell
below LOW_WATERMARK) or SNSPD0 (the detector clicked). irq_latency_cycles later the interrupt
service routine (ISR) runs: it reads IRQ_STATUS, acknowledges the bits with a W1C write, and
handles them. Between interrupts the host makes no bus access at all.

The trade-off against polling:
- Latency: a click is known irq_latency_cycles + one read after it happens, every time.
- CPU cost: 3 reads per click (IRQ_STATUS, COUNT, LAST_TS) and nothing while idle.
- Refill: the watermark must hold enough commands to cover the ISR's reaction time:

      LOW_WATERMARK >= ceil(reaction / cycles_per_command),
      reaction = irq_latency + 3 * bus_latency + 2 * max_jitter + CMD_WORDS - 1

  (IRQ_STATUS read, ack flush, then the first refill frame: last word leaves CMD_WORDS - 1
  cycles after the first and lands one bus latency + jitter later.)

Feedback (measure -> decide -> act): on a click the ISR decides a new AOM0.ENABLE value and pushes
a command stamped click + feedback_latency_cycles. The command is timestamped, so the action
lands exactly on that cycle as long as the frame arrives in time: the budget must cover the
whole ISR path (see min_feedback_latency). Jitter on the way changes nothing.

Like every driver it sees the device only through Bus.read32/write32 and the register map. The
test bench hands it two ways to let time pass: wait_until (run to a cycle) and wait_for_irq (run
until the IRQ line rises; this is the host's interrupt input).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from qtwin.bus import Bus
from qtwin.device.registers import (
    CMD_WORDS,
    FIFO_CAPACITY,
    FIFO_LOW_WATERMARK_RESET,
    IRQ_FIFO_LOW,
    IRQ_SNSPD0,
    REG_MASK,
    Command,
    addr,
)
from qtwin.driver.base import DriverLog, push_frame, read_cycle, start_stream, unwrap_ts

_IRQ_STATUS = addr("SYSTEM.IRQ_STATUS")
_IRQ_ENABLE = addr("SYSTEM.IRQ_ENABLE")
_WATERMARK = addr("FIFO.LOW_WATERMARK")
_COUNT = addr("SNSPD0.COUNT")
_LAST_TS = addr("SNSPD0.LAST_TS")
_HANDLED = IRQ_FIFO_LOW | IRQ_SNSPD0
"""The IRQ bits this driver enables and services."""


def min_feedback_latency(
    irq_latency_cycles: int, bus_latency_cycles: int, max_jitter_cycles: int, decide_cycles: int
) -> int:
    """Smallest feedback_latency_cycles (in cycles) for which the action is never late.

    The ISR path from the click, in order: IRQ latency; IRQ_STATUS read (one bus latency); ack
    flush (bus latency + jitter); COUNT and LAST_TS reads (two bus latencies); the decision;
    the frame's last word leaves CMD_WORDS - 1 cycles after the first and lands one bus latency
    + jitter later. Landing on the timestamp cycle itself is still on time (M5).
    """
    return (
        irq_latency_cycles
        + bus_latency_cycles
        + (bus_latency_cycles + max_jitter_cycles)
        + 2 * bus_latency_cycles
        + decide_cycles
        + (CMD_WORDS - 1)
        + (bus_latency_cycles + max_jitter_cycles)
    )


def min_watermark(
    irq_latency_cycles: int,
    bus_latency_cycles: int,
    max_jitter_cycles: int,
    cycles_per_command: int,
) -> int:
    """Smallest LOW_WATERMARK (in commands) that refills a stream before it starves.

    At the crossing pop, LOW_WATERMARK - 1 commands are left and the first refill command is due
    LOW_WATERMARK * cycles_per_command cycles later. It must land by then. Reaction, in cycles:
    IRQ latency, IRQ_STATUS read, ack flush, then the first frame (see min_feedback_latency).
    cycles_per_command: how fast the sequencer consumes the stream, in cycles per command.
    """
    reaction = (
        irq_latency_cycles
        + bus_latency_cycles
        + (bus_latency_cycles + max_jitter_cycles)
        + (CMD_WORDS - 1)
        + (bus_latency_cycles + max_jitter_cycles)
    )
    return -(-reaction // cycles_per_command)  # ceiling division, integers only


@dataclass(frozen=True)
class Feedback:
    """Click -> AOM action settings.

    feedback_latency_cycles: cycles from the click to the AOM edge; the action lands exactly then.
    decide_cycles: host compute time for the decision, in cycles.
    target: register the action writes (AOM0.ENABLE). Value = COUNT % 2, so successive clicks
        switch the AOM on and off: a decision that depends on what was measured [assumed].
    """

    feedback_latency_cycles: int
    decide_cycles: int
    target: int = addr("AOM0.ENABLE")


class InterruptDriver:
    """Refill and detection by interrupts, with optional click feedback.

    bus: the only path to the device.
    wait_until: lets device time pass up to an absolute cycle (the test bench's run_until).
    wait_for_irq: lets time pass until the IRQ line rises, at most to an absolute cycle; returns
        the rising cycle or None (the test bench's run_until_irq).
    irq_latency_cycles: cycles from the IRQ line rising to the ISR's first instruction (>= 0).
    low_watermark: written to FIFO.LOW_WATERMARK at start, in commands; see min_watermark.
    feedback: click -> AOM action, or None for detection only. Raises ValueError if its budget
        is below min_feedback_latency for this bus.
    """

    def __init__(
        self,
        bus: Bus,
        wait_until: Callable[[int], None],
        wait_for_irq: Callable[[int], int | None],
        irq_latency_cycles: int,
        low_watermark: int = FIFO_LOW_WATERMARK_RESET,
        feedback: Feedback | None = None,
    ) -> None:
        if irq_latency_cycles < 0:
            raise ValueError(f"irq_latency_cycles must be >= 0, got {irq_latency_cycles}")
        if feedback is not None:
            needed = min_feedback_latency(
                irq_latency_cycles,
                bus.bus_latency_cycles,
                bus.max_jitter_cycles,
                feedback.decide_cycles,
            )
            if feedback.feedback_latency_cycles < needed:
                raise ValueError(
                    f"feedback_latency_cycles {feedback.feedback_latency_cycles} < {needed}, "
                    "the ISR path on this bus: the action would be late"
                )
        self._bus = bus
        self._wait_until = wait_until
        self._wait_for_irq = wait_for_irq
        self.irq_latency_cycles = irq_latency_cycles
        self.low_watermark = low_watermark
        self.feedback = feedback
        self._irq_cycles: list[int] = []

    @property
    def irq_cycles(self) -> tuple[int, ...]:
        """Cycle of every IRQ the ISR answered, in order (when the line rose, not ISR entry)."""
        return tuple(self._irq_cycles)

    def _read(self, address: int, now: int) -> tuple[int, int]:
        # One register read issued at host time `now`: (value, host time when it returns).
        return self._bus.read32(address), now + self._bus.bus_latency_cycles

    def run(self, commands: Sequence[Command], until_cycle: int) -> DriverLog:
        """Stream `commands` (timestamp order) and watch SNSPD0, serving IRQs until `until_cycle`.

        Configures the watermark and IRQ_ENABLE, starts the stream (fill the FIFO, set RUN), then
        sleeps until each IRQ. Returns once no IRQ rises before until_cycle (absolute cycles).
        """
        bus = self._bus
        latency, jitter = bus.bus_latency_cycles, bus.max_jitter_cycles
        now = read_cycle(bus) + latency
        # Writes are delivered in order, so both land before the RUN that start_stream writes.
        bus.write32(_WATERMARK, self.low_watermark)
        bus.write32(_IRQ_ENABLE, _HANDLED)
        next_cmd, now = start_stream(bus, self._wait_until, commands, now)
        pushed = next_cmd
        last_count, now = self._read(_COUNT, now)
        detections: list[int] = []
        while True:
            self._wait_until(now)  # finish the previous ISR before sleeping
            irq = self._wait_for_irq(until_cycle)
            if irq is None:
                return DriverLog(detections=tuple(detections), pushed=pushed)
            self._irq_cycles.append(irq)
            now = irq + self.irq_latency_cycles
            self._wait_until(now)
            pending, now = self._read(_IRQ_STATUS, now)
            known_at = now  # the host learns what happened when IRQ_STATUS returns
            handled = pending & _HANDLED
            if handled:
                # Acknowledge first, and only the bits that were read. The ack is a posted write:
                # wait until it has surely landed (latency + max jitter) before reading COUNT.
                # Otherwise a click between the COUNT read and the ack's arrival would have its
                # bit cleared by the ack and never interrupt.
                bus.write32(_IRQ_STATUS, handled)
                now += latency + jitter
                self._wait_until(now)
            if handled & IRQ_SNSPD0:
                count, now = self._read(_COUNT, now)
                last_ts, now = self._read(_LAST_TS, now)
                new_clicks = (count - last_count) & REG_MASK  # modulo 2^32, like the counter
                detections += [known_at] * new_clicks
                last_count = count
                if self.feedback is not None and new_clicks:
                    # LAST_TS was sampled at `now`, after the click: a valid unwrap reference.
                    click = unwrap_ts(last_ts, now)
                    now += self.feedback.decide_cycles
                    action = Command(
                        click + self.feedback.feedback_latency_cycles,
                        self.feedback.target,
                        count % 2,
                    )
                    push_frame(bus, self._wait_until, action, now)
                    now += CMD_WORDS
                    pushed += 1
            if handled & IRQ_FIFO_LOW:
                # No LEVEL read: at the crossing LEVEL < low_watermark, so this many always fit.
                batch = commands[next_cmd : next_cmd + FIFO_CAPACITY - self.low_watermark]
                for cmd in batch:
                    push_frame(bus, self._wait_until, cmd, now)
                    now += CMD_WORDS
                next_cmd += len(batch)
                pushed += len(batch)
