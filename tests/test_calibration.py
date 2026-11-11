"""M6 driver calibration: measure skew, trim it with SKEW_TRIM, detect drift.

Every test calibrates a fresh device that has one scenario's hidden faults. The driver only sees
it through the Bus and the Scope; the test checks the driver's estimates against the hidden truth.
"""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device, LaneTiming
from qtwin.driver.calibration import (
    DRIFT_THRESHOLD_PPB,
    OUTPUT_LANES,
    CalibrationResult,
    calibrate,
)
from qtwin.scenarios import SCENARIOS, calibrate_scenario, run_scenario

SPAN = 1_000_000
QUANTUM_PPB = 10**9 // SPAN
"""One whole cycle of drift over the span, in ppb: the estimate's resolution (1_000 ppb)."""


def _calibrate(timing: dict[str, LaneTiming]) -> tuple[CalibrationResult, Bus]:
    device = Device(timing=timing)
    bus = Bus(device)
    return calibrate(bus, device.scope, device.run_until, span_cycles=SPAN), bus


def test_threshold_is_above_one_quantum() -> None:
    # A single staircase step (1 cycle over the span) must never raise a flag.
    assert DRIFT_THRESHOLD_PPB > QUANTUM_PPB


def test_result_covers_exactly_the_eight_output_lanes() -> None:
    result = calibrate_scenario("ideal")
    assert tuple(result.lanes) == OUTPUT_LANES
    assert len(OUTPUT_LANES) == 8
    assert not any(lane.startswith("SNSPD") for lane in OUTPUT_LANES)


def test_ideal_has_no_skew_no_trim_and_no_false_drift() -> None:
    result = calibrate_scenario("ideal")
    for cal in result.lanes.values():
        assert (cal.skew_cycles, cal.trim_cycles, cal.residual_cycles) == (0, 0, 0)
        assert cal.drift_ppb == 0
        assert not cal.flagged


def test_skew_some_measures_skew_and_trims_every_lane_to_the_slowest() -> None:
    result, bus = _calibrate(SCENARIOS["skew_some"].timing)
    expected_skew = {lane: 0 for lane in OUTPUT_LANES} | {"AOM1": 3, "GPIO2": 1}
    assert {lane: c.skew_cycles for lane, c in result.lanes.items()} == expected_skew
    assert {lane: c.trim_cycles for lane, c in result.lanes.items()} == {
        lane: 3 - s for lane, s in expected_skew.items()
    }
    assert all(c.residual_cycles == 0 for c in result.lanes.values())
    # The trims really are in the device registers, read back through the bus.
    for lane, cal in result.lanes.items():
        assert bus.read32(r.addr(f"{lane}.SKEW_TRIM")) == cal.trim_cycles


@pytest.mark.parametrize("name", ["drift_some", "mixed"])
def test_drifting_lanes_are_flagged_within_one_quantum(name: str) -> None:
    timing = SCENARIOS[name].timing
    result = calibrate_scenario(name)
    flagged = {lane for lane, c in result.lanes.items() if c.flagged}
    assert flagged == {"AOM3", "GPIO0"}
    for lane in flagged:
        assert abs(result.lanes[lane].drift_ppb - timing[lane].drift_ppb) <= QUANTUM_PPB


def test_mixed_still_measures_skew() -> None:
    result = calibrate_scenario("mixed")
    assert result.lanes["AOM1"].skew_cycles == 3
    assert result.lanes["GPIO2"].skew_cycles == 1
    assert all(c.residual_cycles == 0 for c in result.lanes.values())


def test_one_both_skew_from_intercept_drift_from_slope() -> None:
    result = calibrate_scenario("one_both")
    aom2 = result.lanes["AOM2"]
    assert aom2.skew_cycles == 2  # intercept: early edge, drift has not added a cycle yet
    assert abs(aom2.drift_ppb - 30_000) <= QUANTUM_PPB  # slope
    assert aom2.flagged
    others = [c for lane, c in result.lanes.items() if lane != "AOM2"]
    assert all(c.skew_cycles == 0 and not c.flagged for c in others)


def test_drift_below_one_cycle_per_span_is_undetectable() -> None:
    # 900 ppb over 10^6 cycles is 0.9 of a cycle: the Scope only sees whole cycles, so it reads 0.
    result, _ = _calibrate({"AOM0": LaneTiming(drift_ppb=900)})
    assert result.lanes["AOM0"].drift_ppb == 0
    assert not result.lanes["AOM0"].flagged


def test_calibration_times_are_reported() -> None:
    result = calibrate_scenario("ideal")
    assert result.t2 - result.t1 == SPAN
    assert result.threshold_ppb == DRIFT_THRESHOLD_PPB


def test_run_scenario_with_trims_aligns_every_lane_to_the_slowest() -> None:
    s = SCENARIOS["skew_some"]
    trims = {lane: c.trim_cycles for lane, c in calibrate_scenario("skew_some").lanes.items()}
    edges = run_scenario(s.commands, s.timing, trims=trims)
    # Positive-only trim: every lane lands max_skew (3) late, none early, all together.
    assert {e.offset for e in edges} == {3}


def test_calibrating_twice_gives_the_same_trims() -> None:
    # Idempotent: the second run must measure raw skew, not skew + the first run's trims.
    device = Device(timing=SCENARIOS["skew_some"].timing)
    bus = Bus(device)
    first = calibrate(bus, device.scope, device.run_until, span_cycles=SPAN)
    second = calibrate(bus, device.scope, device.run_until, span_cycles=SPAN)
    assert {lane: c.trim_cycles for lane, c in second.lanes.items()} == {
        lane: c.trim_cycles for lane, c in first.lanes.items()
    }
    assert all(c.residual_cycles == 0 for c in second.lanes.values())
