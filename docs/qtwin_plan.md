# qtwin plan (APPROVED DIRECTION, build milestone by milestone)

Rules and decisions live in CLAUDE.md. This file holds the device design and the milestones.
Start with M0. Do not start a milestone before I approve its plan. Each milestone: tests first.

## Device design
Base address 0x4000_0000, one 0x100 block per component:
SYSTEM 0x000, AOM0-3 0x100-0x400, GPIO0-3 0x500-0x800, SNSPD0-1 0x900-0xA00, CLOCK 0xB00, FIFO 0xC00.

| Block | Registers |
|---|---|
| SYSTEM | ID, VERSION, CTRL (run/reset), STATUS (underrun, overflow, late; sticky), IRQ_STATUS (W1C), IRQ_ENABLE |
| CLOCK | FREQ_HZ (RO), CYCLE_LO, CYCLE_HI (RO, 64-bit counter, latched hi/lo read) |
| AOM0..3 | FREQ_WORD, AMPL, PHASE, ENABLE, SKEW_TRIM |
| GPIO0..3 | LEVEL, SKEW_TRIM |
| SNSPD0..1 | COUNT (RO), THRESHOLD, LAST_TS (RO) |
| FIFO | CMD_PUSH (write port), LEVEL (RO), CAPACITY (RO), LOW_WATERMARK |

Access types: RO, RW, W1C (write-1-to-clear). Detector results are produced by a seeded random model
and reported by POLLING (driver reads COUNT/STATUS) or INTERRUPT (IRQ, ISR drains).
Command = (timestamp_cycles, target, value). The sequencer applies it at exactly that cycle on a virtual
clock that jumps from event to event (event-driven, not tick-by-tick).

## Milestones

### M0: Project foundation (~45 min)
git init; `.venv` via `py -3.13 -m venv .venv`; pyproject.toml (setuptools, src layout, requires-python >=3.11,
runtime dep matplotlib, dev extras pytest/mypy/ruff; mypy strict, ruff line length 100, pytest `-ra --strict-markers`);
.gitignore (Python caches, *.egg-info, .idea/, .venv/, build/, out/, cmake-build-*/); docs/ skeleton
(architecture, decisions, limitations, concepts); empty package folders; one real module `timebase.py`.
Concept: integer time, rounding once at the edge with an explicit rule.
Tests first: 4e-9 s -> 1 cycle; 6e-9 s rounds half-even; negative and non-finite input raise;
cycles_to_seconds inverts exactly on whole cycles; ast test that `driver/` never imports `qtwin.device`.
Done when: pytest, mypy, ruff green; 3-4 small commits.

### M1: Register map and register bus
32-bit memory-mapped registers with RO/RW/W1C, reset values, address decoding, layout above.
Concept: memory-mapped I/O, sticky status bits, W1C semantics, why a 64-bit counter needs a latched hi/lo read.
Tests first: reset values; write to RO raises; unmapped address raises; W1C clears only bits written as 1;
CYCLE hi/lo read across a rollover is consistent.

### M2: Command FIFO and event-driven sequencer (ideal scenario)
Bounded FIFO, Command dataclass, sequencer applying commands at exact timestamps; outputs go to a minimal Scope sink.
Concept: timestamped commands separate WHEN software runs from WHEN outputs change; event-driven simulation; "late command".
Tests first: command fires at exactly cycle T; same-timestamp ties keep FIFO order; timestamp in the past sets STATUS.LATE;
same input always gives the same trace.

### M3: Scope module and plots
Scope records (channel, ideal_cycle, actual_cycle, value) for every edge. Helpers: per-channel offset, offset vs time,
edge-to-edge delay. Plots (matplotlib, Agg, saved to out/):
1. logic-analyzer timing diagram of all 10 channels, ideal vs actual edges
2. per-channel skew bar chart, before and after calibration
3. offset vs time per channel with a fitted line (drift)
4. FIFO fill level vs time with underrun/overflow markers
5. bus-jitter histogram
6. latency histograms, polling vs interrupt (p50/p99/max marked, axes in cycles and ns)
Concept: observability, measure what hardware did vs what was commanded.
Tests first: scope captures every edge; analysis checked on hand-built traces; ideal-scenario golden trace
(JSON in tests/golden/) matches; each plot function runs and writes a file (smoke test, no pixel comparison).

### M4: Skew and drift scenarios (fault injection)
Scenario dataclass + registry. Each component has `skew_cycles: int` and `drift_ppb: int`, applied at output time.
```python
def drift_offset(n: int, drift_ppb: int) -> int:
    """Cycles of accumulated drift after n local cycles. Truncates toward zero. n: cycles, drift_ppb: parts per billion."""
    q = abs(n * drift_ppb) // 1_000_000_000
    return q if n * drift_ppb >= 0 else -q
# actual = n + skew_cycles + drift_offset(n, drift_ppb)
```
Example: +50 ppm (50_000 ppb): cycle 19_999 -> 0, cycle 20_000 -> 1, cycle 1_000_000 (4 ms) -> 50. Drift is a staircase of whole-cycle slips.

| Scenario | Configuration |
|---|---|
| ideal | no faults |
| skew_some | AOM1 +3 cycles, GPIO2 +1, SNSPD0 +2 |
| drift_some | AOM3 +50 ppm, GPIO0 -20 ppm |
| mixed | skew on AOM1 and GPIO2, drift on AOM3 and GPIO0 |
| one_both | AOM2: +2 cycles skew AND +30 ppm drift |

Concept: static skew (fixed offset, calibrate once) vs drift (grows with time, needs tracking).
Tests first: hand-computed drift_offset values incl. negative drift, boundary cycles, 64-bit-scale n;
skew shifts an edge by exactly k cycles; one_both offset = skew + drift; one golden trace per scenario; same seed => same trace.

### M5: Bus model: jitter, underrun, overflow
Host writes CMD_PUSH over a bus with named `bus_latency_cycles` and seeded jitter. Scenarios:
`bus_jitter` (uniform 0..N cycles per write), `underrun` (host refills slower than consumption),
`overflow` (burst larger than CAPACITY).
Concept: buffering absorbs jitter; it costs nothing until it drains the FIFO; sticky error flags; drop-or-block policy.
Tests first: with jitter smaller than the FIFO time margin the output trace is bit-identical to ideal (key property);
underrun sets STATUS.UNDERRUN and the IRQ bit at the exact cycle; overflow sets STATUS.OVERFLOW and counts dropped
commands, earlier commands unaffected; same seed => same jitter sequence.

### M6: Driver calibration: measure skew, compensate, detect drift
Driver sees the device only through register reads/writes (+ read-only Scope measurements).
- Skew: command one edge at the same timestamp T on all channels, read arrival times from the Scope,
  skew_i = arrival_i - T; set SKEW_TRIM_i = max_skew - skew_i (positive-only delay); re-measure, check residual.
- Drift: repeat the calibration edge at T1 and T2 far apart (e.g. 10^6 cycles). Constant offset = skew, changing offset = drift.
  ppb ~= (off2 - off1) * 10^9 // (T2 - T1), integers only. Above a named threshold the channel is flagged.
  In one_both: intercept = skew, slope = drift.
Concept: closed-loop calibration, measurement resolution limits, intercept vs slope.
Tests first: skew_some -> residual 0 on all channels; ideal -> no drift flagged (no false positives);
drift_some and one_both -> right channels flagged, estimate within +-1 cycle quantization;
drift below 1 cycle per (T2 - T1) is undetectable (assert it, document in limitations.md).

### M7: Polling driver vs interrupt driver
Same virtual clock. Polling: read FIFO.LEVEL every `poll_period_cycles`, each read costs `bus_latency_cycles`.
Interrupt: IRQ when FIFO level drops below LOW_WATERMARK, ISR runs after `irq_latency_cycles`.
Also the SNSPD feedback path: click -> IRQ -> decide -> conditional AOM command, total latency =
`feedback_latency_cycles`, measured on the Scope.
Concept: latency vs CPU cost, watermark sizing, measure -> decide -> act loop.
Tests first: IRQ fires at the watermark crossing, not before; fixed scenario where slow polling underruns but interrupt does not;
bus-read counts (polling >> interrupt); feedback action lands exactly feedback_latency_cycles after the click.

### M8: C++ SPSC ring buffer (last, optional if time is short)
Header-only `SpscRingBuffer<T, N>`: power-of-two capacity, head/tail on separate cache lines, acquire/release ordering
documented on every atomic. Two-thread latency benchmark writing CSV + Python script plotting it. CMake puts the binary at
`./build/ringbuffer_bench` with any generator.
Concept: real-thread version of M2's FIFO: memory ordering, false sharing, why SPSC needs no locks.
Tests first (ctest, assert-based, no GoogleTest): empty/full detection, wraparound, capacity-1 edge case,
two-thread stress test pushing 0..10^7 in order with none lost.

## Priority if time runs short
M0-M4 (registers, scope, skew/drift, plots) are the core. M5-M7 add real-time depth. M8 is optional.
