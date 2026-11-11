"""Scenarios: fixed command lists plus injected timing faults, for tests, golden traces and plots.

A scenario is (commands, per-lane LaneTiming). `ideal` keeps M3's short list so its golden trace
stays unchanged; the fault scenarios use long_commands(), which reaches 1_000_000 cycles (4 ms) so
that drift has time to show up (50 ppm over 10^6 cycles = 50 cycles).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device, LaneTiming
from qtwin.scope import Edge

PULSE_START_CYCLES: int = 1_000
"""When the first pulses begin, in cycles (4 us). Setup writes go at cycle 0, before it."""

MARKER_CYCLES: tuple[int, ...] = (250_000, 500_000, 750_000, 1_000_000)
"""When long_commands() writes one marker on every output lane, in cycles (1, 2, 3, 4 ms)."""


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


def long_commands() -> list[r.Command]:
    """ideal_commands() plus one marker per output lane at each MARKER_CYCLES time.

    AOM marker: a PHASE write (value 1..4, arbitrary [assumed]); GPIO marker: LEVEL toggles 1, 0,
    1, 0. 24 + 4 * 8 = 56 commands, so the whole list still fits the 64-entry FIFO before RUN.
    """
    cmds = ideal_commands()
    for k, t in enumerate(MARKER_CYCLES):
        cmds += [r.Command(t, r.addr(f"AOM{i}.PHASE"), k + 1) for i in range(4)]
        cmds += [r.Command(t, r.addr(f"GPIO{i}.LEVEL"), (k + 1) % 2) for i in range(4)]
    return sorted(cmds, key=lambda c: c.timestamp_cycles)


def run_scenario(
    commands: Sequence[r.Command], timing: Mapping[str, LaneTiming] | None = None
) -> tuple[Edge, ...]:
    """Push `commands` through Bus/CMD_PUSH into a fresh Device with the given lane `timing`,
    start it, run to the last timestamp and return the Scope trace. The FIFO is filled before RUN,
    so the list must fit. Edges come out in issue order (ideal timestamp, FIFO ties); with skew,
    actual_cycle can go backwards from one edge to the next across lanes.
    """
    device = Device(timing=timing)
    bus = Bus(device)
    for cmd in commands:
        for word in r.encode_command(cmd):
            bus.write32(r.addr("FIFO.CMD_PUSH"), word)
    bus.write32(r.addr("SYSTEM.CTRL"), r.CTRL_RUN)
    device.run_until(max((c.timestamp_cycles for c in commands), default=0))
    return device.scope.edges


@dataclass(frozen=True)
class Scenario:
    """A named, fixed experiment: what the host sends and which faults the device has.

    commands: in timestamp order, as pushed to CMD_PUSH.
    timing: injected faults per lane ("AOM1"); lanes not listed are ideal.
    """

    name: str
    description: str
    commands: tuple[r.Command, ...]
    timing: dict[str, LaneTiming] = field(default_factory=dict)


_SKEW = {"AOM1": LaneTiming(skew_cycles=3), "GPIO2": LaneTiming(skew_cycles=1)}
_DRIFT = {"AOM3": LaneTiming(drift_ppb=50_000), "GPIO0": LaneTiming(drift_ppb=-20_000)}

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("ideal", "no faults", tuple(ideal_commands())),
        Scenario(
            "skew_some",
            "AOM1 +3 cycles, GPIO2 +1, SNSPD0 +2 (SNSPD has no edges until M7)",
            tuple(long_commands()),
            {**_SKEW, "SNSPD0": LaneTiming(skew_cycles=2)},
        ),
        Scenario("drift_some", "AOM3 +50 ppm, GPIO0 -20 ppm", tuple(long_commands()), dict(_DRIFT)),
        Scenario(
            "mixed",
            "skew_some's AOM1/GPIO2 skew plus drift_some's drift [assumed values]",
            tuple(long_commands()),
            {**_SKEW, **_DRIFT},
        ),
        Scenario(
            "one_both",
            "AOM2: +2 cycles skew AND +30 ppm drift",
            tuple(long_commands()),
            {"AOM2": LaneTiming(skew_cycles=2, drift_ppb=30_000)},
        ),
    )
}
"""Every scenario by name, in the order of the plan table."""
