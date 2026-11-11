"""Driver calibration: measure each output lane's skew, trim it out, and detect drift.

The driver sees the device only through Bus.read32/write32 and the read-only Scope trace, the way
a real host sees an FPGA box through its registers plus a lab oscilloscope. Sequence:

1. One calibration edge on all 8 output lanes at the same cycle T1. skew_i = arrival_i - T1.
2. Write SKEW_TRIM_i = max_skew - skew_i. Trims can only delay, so every lane is aligned to the
   slowest one, not to T1.
3. A verify edge at T1 + lead: residual_i = offset_i - max_skew must be 0 (closed loop).
4. A drift edge at T2 = T1 + span. A constant offset is skew (intercept); an offset that grew
   between T1 and T2 is drift (slope). Drift is reported and flagged, never compensated.

SNSPD lanes are inputs with no output edge and no SKEW_TRIM, so they are not calibrated (until M7).
"""

from collections.abc import Callable
from dataclasses import dataclass

from qtwin.bus import Bus
from qtwin.device.registers import CTRL_RUN, Command, addr, encode_command
from qtwin.scope import Scope, offsets_at

OUTPUT_LANES: tuple[str, ...] = (*(f"AOM{i}" for i in range(4)), *(f"GPIO{i}" for i in range(4)))
"""Lanes with an output edge and a SKEW_TRIM register, in display order."""

# The register each lane's calibration edge writes. Any output register would do; these are
# readable, so the driver can write back the current value and leave the output state unchanged.
_EDGE_REGISTER: dict[str, str] = {
    **{f"AOM{i}": f"AOM{i}.PHASE" for i in range(4)},
    **{f"GPIO{i}": f"GPIO{i}.LEVEL" for i in range(4)},
}

PPB: int = 1_000_000_000
"""Parts per billion in one."""

DRIFT_THRESHOLD_PPB: int = 2_000
"""Flag a lane when |drift estimate| is above this, in ppb (2 ppm) [assumed].

Must be above one quantum (10**9 // span_cycles = 1_000 ppb for the default span): a single
whole-cycle step of the drift staircase must not count as drift, or ideal lanes could be flagged.
"""


@dataclass(frozen=True)
class LaneCalibration:
    """What calibration found for one lane. All cycles are 4 ns device cycles.

    skew_cycles: measured offset of the T1 edge (before trimming).
    trim_cycles: value written to SKEW_TRIM (max skew - skew_cycles), >= 0.
    residual_cycles: verify-edge offset minus max skew; 0 means aligned with the slowest lane.
    drift_ppb: drift estimate in parts per billion; resolution 10**9 // span ppb.
    flagged: True if |drift_ppb| > the threshold.
    """

    lane: str
    skew_cycles: int
    trim_cycles: int
    residual_cycles: int
    drift_ppb: int
    flagged: bool


@dataclass(frozen=True)
class CalibrationResult:
    """t1, t2: cycles of the skew and drift calibration edges. threshold_ppb: flag level used."""

    t1: int
    t2: int
    threshold_ppb: int
    lanes: dict[str, LaneCalibration]


def calibrate(
    bus: Bus,
    scope: Scope,
    wait_until: Callable[[int], None],
    *,
    lead_cycles: int = 1_000,
    span_cycles: int = 1_000_000,
    drift_threshold_ppb: int = DRIFT_THRESHOLD_PPB,
) -> CalibrationResult:
    """Calibrate every output lane; leaves the device running with the trims written.

    wait_until(cycle): lets device time pass up to absolute `cycle`. The driver can't advance the
        device clock itself, so the caller supplies it, like a host sleeping until a time.
    lead_cycles: how far ahead of now the T1 edge is scheduled, and the gap to the verify edge
        (cycles). Small, so drift has hardly added to the skew reading.
    span_cycles: T2 - T1 in cycles. Drift below one cycle per span (10**9 // span ppb) reads as 0.
    drift_threshold_ppb: flag level in ppb; should be above 10**9 // span_cycles.
    """
    if not 0 < lead_cycles < span_cycles:
        raise ValueError(f"need 0 < lead_cycles < span_cycles, got {lead_cycles}, {span_cycles}")
    ctrl = addr("SYSTEM.CTRL")
    bus.write32(ctrl, bus.read32(ctrl) | CTRL_RUN)

    t1 = _now(bus) + lead_cycles
    skew = _measure(bus, scope, wait_until, t1)
    max_skew = max(skew.values())
    trim = {lane: max_skew - s for lane, s in skew.items()}
    for lane, value in trim.items():
        bus.write32(addr(f"{lane}.SKEW_TRIM"), value)

    after = _measure(bus, scope, wait_until, t1 + lead_cycles)
    t2 = t1 + span_cycles
    late = _measure(bus, scope, wait_until, t2)

    lanes: dict[str, LaneCalibration] = {}
    for lane in OUTPUT_LANES:
        # The T2 edge includes the trim the driver wrote; subtract it so both readings compare
        # the lane's own delay. What is left beyond the skew is drift accumulated over the span.
        growth = (late[lane] - trim[lane]) - skew[lane]
        ppb = growth * PPB // span_cycles
        lanes[lane] = LaneCalibration(
            lane=lane,
            skew_cycles=skew[lane],
            trim_cycles=trim[lane],
            residual_cycles=after[lane] - max_skew,
            drift_ppb=ppb,
            flagged=abs(ppb) > drift_threshold_ppb,
        )
    return CalibrationResult(t1=t1, t2=t2, threshold_ppb=drift_threshold_ppb, lanes=lanes)


def _now(bus: Bus) -> int:
    # Reading CYCLE_LO latches the high half, so LO then HI is one consistent 64-bit value.
    lo = bus.read32(addr("CLOCK.CYCLE_LO"))
    hi = bus.read32(addr("CLOCK.CYCLE_HI"))
    return (hi << 32) | lo


def _measure(
    bus: Bus, scope: Scope, wait_until: Callable[[int], None], cycle: int
) -> dict[str, int]:
    """Put one edge on every output lane at `cycle`, let it happen, return offsets in cycles."""
    for lane in OUTPUT_LANES:
        target = addr(_EDGE_REGISTER[lane])
        cmd = Command(timestamp_cycles=cycle, target=target, value=bus.read32(target))
        for word in encode_command(cmd):
            bus.write32(addr("FIFO.CMD_PUSH"), word)
    wait_until(cycle)
    offsets = offsets_at(scope.edges, cycle)
    missing = [lane for lane in OUTPUT_LANES if lane not in offsets]
    if missing:
        raise RuntimeError(f"no calibration edge at cycle {cycle} on {missing}")
    return {lane: offsets[lane] for lane in OUTPUT_LANES}
