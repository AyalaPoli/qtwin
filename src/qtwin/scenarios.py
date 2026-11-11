"""Scenarios: fixed command lists plus injected timing faults, for tests, golden traces and plots.

A scenario is (commands, per-lane LaneTiming, bus timing, host model). `ideal` keeps M3's short
list so its golden trace stays unchanged; the fault scenarios use long_commands(), which reaches
1_000_000 cycles (4 ms) so that drift has time to show up (50 ppm over 10^6 cycles = 50 cycles).
The M5 bus scenarios stream their commands through a slow, jittery bus while the device runs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from qtwin.bus import Bus, BusWrite
from qtwin.device import registers as r
from qtwin.device.device import Device, LaneTiming
from qtwin.driver.base import stream
from qtwin.driver.calibration import CalibrationResult, calibrate
from qtwin.scope import Edge

PULSE_START_CYCLES: int = 1_000
"""When the first pulses begin, in cycles (4 us). Setup writes go at cycle 0, before it."""

MARKER_CYCLES: tuple[int, ...] = (250_000, 500_000, 750_000, 1_000_000)
"""When long_commands() writes one marker on every output lane, in cycles (1, 2, 3, 4 ms)."""

STREAM_START_CYCLES: int = 10_000
"""First timestamp of the streamed command lists, in cycles (40 us): leaves room for any lead."""


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


_LANE_REGISTERS: tuple[int, ...] = (
    *(r.addr(f"AOM{i}.PHASE") for i in range(4)),
    *(r.addr(f"GPIO{i}.LEVEL") for i in range(4)),
)


def _round_robin(count: int, start: int, spacing: int) -> list[r.Command]:
    # Command i goes to lane i % 8 with value i, so every edge in a trace is distinguishable.
    return [
        r.Command(start + spacing * i, _LANE_REGISTERS[i % len(_LANE_REGISTERS)], i)
        for i in range(count)
    ]


def stream_commands() -> list[r.Command]:
    """40 commands 100 cycles apart from STREAM_START_CYCLES, round-robin over the 8 output lanes.

    Spacing 100 >= CMD_WORDS, so the host is never behind and the margin alone decides lateness.
    Values are arbitrary but fixed [assumed]. Fits the FIFO, so it can also be prefilled.
    """
    return _round_robin(40, STREAM_START_CYCLES, 100)


def underrun_commands() -> list[r.Command]:
    """12 commands only 20 cycles apart: faster than a host that needs 40 cycles per command."""
    return _round_robin(12, STREAM_START_CYCLES, 20)


def overflow_commands() -> list[r.Command]:
    """A burst of FIFO_CAPACITY + 6 commands, 100 cycles apart from cycle 1_000."""
    return _round_robin(r.FIFO_CAPACITY + 6, 1_000, 100)


@dataclass(frozen=True)
class BusConfig:
    """Bus timing for a scenario: latency and max jitter in cycles, jitter seed. Default instant."""

    bus_latency_cycles: int = 0
    max_jitter_cycles: int = 0
    seed: int = 0


@dataclass(frozen=True)
class StreamConfig:
    """Host streamer settings for driver.base.stream, in cycles."""

    lead_cycles: int
    host_gap_cycles: int = r.CMD_WORDS


@dataclass(frozen=True)
class Scenario:
    """A named, fixed experiment: what the host sends and which faults the device has.

    commands: in timestamp order, as pushed to CMD_PUSH.
    timing: injected faults per lane ("AOM1"); lanes not listed are ideal.
    bus: bus latency, jitter and seed; default instant (M1-M4 behaviour).
    stream: None = fill the FIFO before RUN (M2-M4 path); else the host streams while running.
    """

    name: str
    description: str
    commands: tuple[r.Command, ...]
    timing: dict[str, LaneTiming] = field(default_factory=dict)
    bus: BusConfig = BusConfig()
    stream: StreamConfig | None = None


@dataclass(frozen=True)
class RunResult:
    """Everything one run produced. Cycles are absolute device cycles (4 ns).

    edges: the Scope trace in issue order (ideal timestamp, FIFO ties); with skew, actual_cycle
        can go backwards from one edge to the next across lanes.
    status: SYSTEM.STATUS at the end (sticky error bits). dropped: FIFO.DROPPED at the end.
    fifo_levels, status_events: Device instrumentation, (cycle, level) and (cycle, mask).
    bus_writes: every bus write with its issue and arrival cycle.
    """

    edges: tuple[Edge, ...]
    status: int
    dropped: int
    fifo_levels: tuple[tuple[int, int], ...]
    status_events: tuple[tuple[int, int], ...]
    bus_writes: tuple[BusWrite, ...]


def run(scenario: Scenario, trims: Mapping[str, int] | None = None) -> RunResult:
    """Run `scenario` on a fresh Device through Bus/CMD_PUSH, the real path, until the last
    command has run and the last bus write has landed.

    trims: SKEW_TRIM value in cycles per lane ("AOM1"), written over the bus before any command,
        e.g. the trims a calibration found. Lanes not listed keep the reset value 0.
    """
    device = Device(timing=scenario.timing)
    b = scenario.bus
    bus = Bus(device, b.bus_latency_cycles, b.max_jitter_cycles, b.seed)
    for lane, trim in (trims or {}).items():
        bus.write32(r.addr(f"{lane}.SKEW_TRIM"), trim)
    ctrl = r.addr("SYSTEM.CTRL")
    if scenario.stream is None:
        for cmd in scenario.commands:
            for word in r.encode_command(cmd):
                bus.write32(r.addr("FIFO.CMD_PUSH"), word)
        bus.write32(ctrl, r.CTRL_RUN)
    else:
        bus.write32(ctrl, r.CTRL_RUN)
        s = scenario.stream
        stream(bus, scenario.commands, device.run_until, s.lead_cycles, s.host_gap_cycles)
    # A late command runs when its frame lands, which can be after the last timestamp.
    end = max(
        [c.timestamp_cycles for c in scenario.commands]
        + [w.arrival_cycle for w in bus.writes]
        + [device.clock.now]
    )
    device.run_until(end)
    return RunResult(
        edges=device.scope.edges,
        status=bus.read32(r.addr("SYSTEM.STATUS")),
        dropped=bus.read32(r.addr("FIFO.DROPPED")),
        fifo_levels=device.fifo_levels,
        status_events=device.status_events,
        bus_writes=bus.writes,
    )


def run_scenario(
    commands: Sequence[r.Command],
    timing: Mapping[str, LaneTiming] | None = None,
    trims: Mapping[str, int] | None = None,
) -> tuple[Edge, ...]:
    """Scope trace of `commands` prefilled into the FIFO over an instant bus, then RUN: the ideal
    reference path. Commands beyond FIFO_CAPACITY are dropped. See run() for `trims`.
    """
    adhoc = Scenario("adhoc", "", tuple(commands), dict(timing or {}))
    return run(adhoc, trims).edges


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
        Scenario(
            "bus_jitter",
            "streamed over a 20-cycle bus with 0..30 jitter, lead 60: margin 6, no misses",
            tuple(stream_commands()),
            bus=BusConfig(bus_latency_cycles=20, max_jitter_cycles=30, seed=1),
            stream=StreamConfig(lead_cycles=60),
        ),
        Scenario(
            "underrun",
            "host needs 40 cycles per command, commands 20 apart: falls behind from command 5",
            tuple(underrun_commands()),
            bus=BusConfig(bus_latency_cycles=10),
            stream=StreamConfig(lead_cycles=100, host_gap_cycles=40),
        ),
        Scenario(
            "overflow",
            f"burst of {r.FIFO_CAPACITY + 6} commands prefilled: {r.FIFO_CAPACITY} fit, 6 dropped",
            tuple(overflow_commands()),
        ),
    )
}
"""Every scenario by name: the M4 plan table, then the M5 bus scenarios."""


def calibrate_scenario(name: str) -> CalibrationResult:
    """Run M6 calibration on a fresh Device with scenario `name`'s hidden lane faults.

    Only the scenario's timing is used, not its commands: calibration sends its own edges. This is
    the test bench side: it owns the device and hands the driver a bus, the scope and a way to let
    time pass (device.run_until). Raises KeyError for an unknown name.
    """
    device = Device(timing=SCENARIOS[name].timing)
    return calibrate(Bus(device), device.scope, device.run_until)
