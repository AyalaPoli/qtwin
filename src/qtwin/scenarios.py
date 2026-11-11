"""Scenarios: fixed command lists that drive the device for tests, golden traces and plots.

M3 version: only the ideal scenario. The Scenario dataclass, fault parameters (skew, drift) and
the registry arrive in M4.
"""

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.scope import Edge

PULSE_START_CYCLES: int = 1_000
"""When the first pulses begin, in cycles (4 us). Setup writes go at cycle 0, before it."""


def ideal_commands() -> list[r.Command]:
    """A small fixed pattern on all 8 output lanes, in timestamp order.

    Cycle 0: every AOM gets a frequency word and amplitude (setup).
    Each AOM's ENABLE pulses for 2000 cycles, starts staggered by 250 cycles.
    All 4 GPIOs rise together at PULSE_START_CYCLES (a same-cycle tie, also with AOM0 ENABLE)
    and fall 500 cycles apart. Values are arbitrary but fixed [assumed].
    """
    cmds: list[r.Command] = []
    for i in range(4):
        cmds.append(r.Command(0, r.addr(f"AOM{i}.FREQ_WORD"), 0x1000_0000 * (i + 1)))
        cmds.append(r.Command(0, r.addr(f"AOM{i}.AMPL"), 0x8000))
    for i in range(4):
        start = PULSE_START_CYCLES + 250 * i
        cmds.append(r.Command(start, r.addr(f"AOM{i}.ENABLE"), 1))
        cmds.append(r.Command(start + 2_000, r.addr(f"AOM{i}.ENABLE"), 0))
    for i in range(4):
        cmds.append(r.Command(PULSE_START_CYCLES, r.addr(f"GPIO{i}.LEVEL"), 1))
        cmds.append(r.Command(PULSE_START_CYCLES + 500 * (i + 1), r.addr(f"GPIO{i}.LEVEL"), 0))
    # The FIFO never reorders, so the host must push in time order. sorted() is stable, which
    # keeps the order above for ties.
    return sorted(cmds, key=lambda c: c.timestamp_cycles)


def run_scenario(commands: list[r.Command]) -> tuple[Edge, ...]:
    """Push `commands` through Bus/CMD_PUSH into a fresh Device, start it, run to the last
    timestamp and return the Scope trace. The FIFO is filled before RUN, so it must fit.
    """
    device = Device()
    bus = Bus(device)
    for cmd in commands:
        for word in r.encode_command(cmd):
            bus.write32(r.addr("FIFO.CMD_PUSH"), word)
    bus.write32(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    device.run_until(max((c.timestamp_cycles for c in commands), default=0))
    return device.scope.edges
