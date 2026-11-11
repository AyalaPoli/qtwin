"""Driver building blocks shared by every host driver: read the clock, push and stream commands,
unwrap 32-bit timestamps.

Like all driver code, this sees the device only through Bus.read32/write32. Time passes through
the `wait_until` callback the test bench hands in (see calibration.py), never by touching the
device.

The streamer (M5) is the host side of a FIFO: it pushes each command lead_cycles before its
timestamp, so the command is already queued when the sequencer needs it. Whether that lead is
enough is the margin:

    margin = lead_cycles - (bus_latency_cycles + max_jitter_cycles + CMD_WORDS)

The last of the CMD_WORDS words leaves at start + CMD_WORDS - 1 and arrives at most
bus_latency_cycles + max_jitter_cycles later (in-order delivery never makes it later than that:
earlier words have earlier worst cases). A frame arriving on its own timestamp cycle is still on
time, so margin >= -1 guarantees an on-time command; margin >= 0 leaves one spare cycle.
"""

from collections.abc import Callable, Sequence

from qtwin.bus import Bus
from qtwin.device.registers import CMD_WORDS, REG_MASK, Command, addr, encode_command

_CMD_PUSH = addr("FIFO.CMD_PUSH")


def read_cycle(bus: Bus) -> int:
    """The device's 64-bit cycle counter, in cycles."""
    # Reading CYCLE_LO latches the high half, so LO then HI is one consistent 64-bit value.
    lo = bus.read32(addr("CLOCK.CYCLE_LO"))
    hi = bus.read32(addr("CLOCK.CYCLE_HI"))
    return (hi << 32) | lo


def margin_cycles(lead_cycles: int, bus_latency_cycles: int, max_jitter_cycles: int) -> int:
    """Spare cycles between a command's worst-case arrival and its timestamp (all in cycles).

    >= -1 means every command is on time, as long as the host is not behind (see stream).
    """
    return lead_cycles - (bus_latency_cycles + max_jitter_cycles + CMD_WORDS)


def stream(
    bus: Bus,
    commands: Sequence[Command],
    wait_until: Callable[[int], None],
    lead_cycles: int,
    host_gap_cycles: int = CMD_WORDS,
) -> tuple[int, ...]:
    """Push `commands` (in timestamp order) over CMD_PUSH, each starting lead_cycles before its
    timestamp. Returns the start cycle of each command. Does not touch CTRL.RUN.

    start_i = max(ts_i - lead_cycles, start_{i-1} + host_gap_cycles, now); word k goes out at
    start_i + k, one bus write per cycle.
    wait_until(cycle): lets device time pass up to absolute `cycle` (the test bench's run_until).
    lead_cycles: how early each command is pushed, in cycles.
    host_gap_cycles: minimum cycles between two command starts: how fast the host can produce
        commands. Commands closer together than this put the host behind, eating the margin
        (the underrun scenario). Must be >= CMD_WORDS, since a frame takes CMD_WORDS cycles.
    """
    if host_gap_cycles < CMD_WORDS:
        raise ValueError(f"host_gap_cycles must be >= {CMD_WORDS}, got {host_gap_cycles}")
    starts: list[int] = []
    for cmd in commands:
        start = cmd.timestamp_cycles - lead_cycles
        if starts:
            start = max(start, starts[-1] + host_gap_cycles)
        # The host can't act in the past: an early command it is already late for goes out now.
        # read_cycle samples at its CYCLE_LO read; the CYCLE_HI read costs one more bus latency.
        start = max(start, read_cycle(bus) + bus.bus_latency_cycles)
        push_frame(bus, wait_until, cmd, start)
        starts.append(start)
    return tuple(starts)


def push_frame(bus: Bus, wait_until: Callable[[int], None], cmd: Command, start: int) -> None:
    """Write `cmd` to CMD_PUSH: word k at absolute cycle start + k, one bus write per cycle.

    start: first write cycle; must not be in the past (wait_until raises if it is).
    """
    for k, word in enumerate(encode_command(cmd)):
        wait_until(start + k)
        bus.write32(_CMD_PUSH, word)


def unwrap_ts(last_ts: int, t_ref: int) -> int:
    """Full 64-bit cycle of a click from its 32-bit LAST_TS and a 64-bit time t_ref at or after it.

    LAST_TS keeps the low 32 bits of the click cycle, so it wraps every 2^32 cycles (17.18 s).
    The click is the latest cycle <= t_ref with those low bits: correct while the click is less
    than 2^32 cycles before t_ref. An ISR reading LAST_TS microseconds after the IRQ is far inside
    that window. last_ts: 0..2^32-1. t_ref: absolute cycles.
    """
    if not 0 <= last_ts <= REG_MASK:
        raise ValueError(f"last_ts must fit in 32 bits, got {last_ts}")
    # Python's % is never negative, so this also works when t_ref's low bits have wrapped past
    # last_ts (e.g. click at 2^32 - 5, t_ref at 2^32 + 10): the distance comes out as 15.
    return t_ref - ((t_ref & REG_MASK) - last_ts) % (REG_MASK + 1)
