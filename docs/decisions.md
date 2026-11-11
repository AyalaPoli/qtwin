# Design decisions

## Decided before M0 (recorded from CLAUDE.md)
- **Layout without sim/ or hal/.** The model is organised as the hardware is: `device/`, `bus.py`,
  `driver/`, `scope.py`. The earlier api/compiler layer and camera/atom-loss model were dropped.
  An optional thin "experiment -> Command list" builder may come after M7.
- **Whole 4 ns cycles everywhere**, including skew and Scope resolution. Simpler and exact.
  Rejected: a second, finer integer time unit (realistic, but two units to explain).
- **Skew compensation with `SKEW_TRIM` registers**, which are positive-only delay lines. Every channel is
  aligned to the slowest: `SKEW_TRIM_i = max_skew - skew_i`. Rejected: shifting command timestamps in the
  driver, because it eats FIFO time margin and ties calibration to scheduling.
- **Drift in integer ppb**, `drift_offset(n, ppb)` truncated toward zero so that +/- drift is symmetric.
  Detected in M6; continuous compensation is a next step.
- **A write to an RO register raises.** Rejected: silently ignoring it as hardware does, which hides driver bugs.

## M0: Project foundation
- **What:** setuptools src-layout package, pytest/mypy (strict)/ruff, `timebase.py`, and the ast import-boundary test.
- **Rounding:** `round(Fraction(str(seconds)) * clock_hz)`, which is exact and rounds half to even.
  Rejected: float multiplication (`round(6e-9 * 250e6)` depends on binary representation error);
  `Fraction(seconds)` without `str` (6e-9 becomes 1.4999... cycles and rounds to 1); `Decimal`
  (works, but needs a context/quantize setup and is harder to explain).
- **Half-even, not half-up:** it has no systematic upward bias when many durations are rounded, and it is
  Python's built-in `round()` behaviour.
- **Boundary test is static (ast):** it catches imports in code that no test runs. The checker has its
  own tests with known-bad snippets, so it can't pass vacuously while `driver/` is empty.
- **Ruff format skips Markdown:** recent ruff versions reformat code blocks inside `.md` files, and
  the hand-written docs should stay as written.

## M1: Register map and register bus
- **What:** `device/registers.py` (pure-data map), `device/clock.py` (64-bit counter),
  `device/device.py` (storage, decoding, access rules, latch, reset, IRQ line), and `bus.py`
  (`read32`/`write32`, 0 cycles of latency until M5).
- **The driver may import `qtwin.device.registers`.** CLAUDE.md allows it, but the M0 boundary test
  rejected it. The checker now allows exactly that absolute path, and a test keeps `registers.py`
  stdlib-only so the import can't reach device state. Rejected: moving the map out of `device/`
  (changes the decided layout); re-exporting it through `bus.py` (hides where registers come from).
- **Strict bus:** unaligned/unmapped address, RO write and WO read raise `BusError`; a value outside
  32 bits raises `ValueError`. Rejected: masking to 32 bits (hides driver bugs, same reason as RO).
- **STATUS is W1C** [assumed], like IRQ_STATUS. An error sets the bit in both, and they are cleared
  separately (clear the flag vs. acknowledge the interrupt). Rejected: RO STATUS cleared only by
  reset (the host could never acknowledge one error and keep running).
- **CYCLE_HI's stored value is the shadow.** A CYCLE_LO read writes the high half there, so there is
  no separate shadow field. Reading HI first returns the last latched value.
- **CTRL.RESET self-clears** and wins over RUN in the same write. Reset also zeroes the clock.
- **The IRQ line is computed** from IRQ_STATUS & IRQ_ENABLE, so it can never go stale.
- **The clock raises at 2^64** instead of wrapping [assumed]: no run gets close, so reaching it is a bug.
- Rejected: a separate `RegisterFile` class. With one device, `Device` holding the dict is simpler.

## M2: Command FIFO and event-driven sequencer
- **What:** `Command` and the frame helpers in `registers.py`, `device/fifo.py`, `device/sequencer.py`,
  minimal `scope.py`, and the wiring in `device.py` (`push_command`, `run_until`, computed FIFO.LEVEL).
- **`Command` lives in `registers.py`.** The frame format is part of the register interface, and the
  driver may import `registers` but not the rest of `qtwin.device`. Rejected: a new top-level module
  (would widen the driver's allowed imports).
- **Target = absolute register address.** One address space, no second channel numbering. Only output
  registers are accepted. Rejected: a packed channel/field id (needs a translation table to explain).
- **4-word frame, committed on the last word** [assumed]. A 64-bit timestamp needs two words, plus
  target and value. Committing at the end means a half-sent frame can never run.
- **Late = execute now and flag** [assumed]. Rejected: drop. Dropping a late "beam off" would leave
  the beam on; running it late keeps the outputs consistent and the sticky flag reports the miss.
- **No sorting.** Like hardware, the FIFO never reorders. A smaller timestamp queued behind a larger
  one goes late, which shows head-of-line blocking.
- **FIFO.LEVEL computed** from the FIFO, like the IRQ line, so it can't go stale.
- **Sequencer runs only while CTRL.RUN is set; the clock always counts** [assumed]. The host can fill
  the FIFO and then start. Commands that come due while stopped run late after RUN.
- **Full FIFO raises** for now; drop + OVERFLOW is M5.
- **The Scope records every applied write**, even one that doesn't change the value, so ties stay
  visible. CTRL.RESET does not clear it (it is an instrument outside the device).

## M3: Scope and plots (reduced scope)
- **What:** `Edge(channel, ideal_cycle, actual_cycle, value)`, helpers in `scope.py`
  (`offset_vs_time`, `channel_offsets`, `edge_delay`), `analysis/stats.py` (`linear_fit`),
  minimal `scenarios.py` (ideal only), golden trace, three plots in `analysis/plots.py`.
- **Edge stores both cycles**, not just an offset, so plots can draw what was commanded next to what
  happened. A late command is recorded honestly with actual > ideal.
- **Field order changed**, so existing tests now build Edge with keyword arguments. The tests were not
  wrong; the interface changed.
- **Channel = register name; lane = component block** (`component("AOM1.AMPL") == "AOM1"`). Ten lanes:
  4 AOM, 4 GPIO, 2 SNSPD. One address space, no second channel numbering.
- **Per-channel offset = earliest edge** on the lane, the closest single reading of static skew
  (drift has had least time to add to it). Rejected: mean (mixes drift into skew).
- **`edge_delay` uses actual cycles** and the first dst edge at or after each src edge; an unmatched
  src edge is skipped. This is the shape M7's click -> AOM latency needs.
- **Helpers are integer; only `linear_fit` uses floats**, and only for plots.
- **Golden trace:** `tests/golden/ideal.json`, exact comparison. Regenerate with
  `$env:QTWIN_REGEN_GOLDEN=1; pytest tests/test_golden.py`; that run still fails, so a regeneration
  is never silent, then review `git diff tests/golden`.
- **`scenarios.py` holds only `ideal_commands()` and `run_scenario()`** for now (confirmed); the Scenario
  dataclass and registry are M4. `run_scenario` goes through Bus/CMD_PUSH, the real path.
- **Plots use `Figure` + explicit Agg canvas, not pyplot**: no global state, no GUI backend.
  Offset vs time is small multiples (one panel per lane, shared axes) instead of 10 colors on one chart.
  `offset_bars` takes a label -> trace mapping, so M6's "after calibration" is a second entry.
- **Deferred:** FIFO fill level (M5), bus-jitter histogram (M5) and polling vs interrupt latency
  histograms (M7). Their data doesn't exist yet. Plotting them now would mean inventing it.
- **Ideal scenario values and timings are arbitrary** but fixed (confirmed). They only need to cover
  all 8 output lanes, include a same-cycle tie and fit the FIFO.
- **Lanes with no edges are labelled "no edges"** in the offset bar chart (confirmed). Zero offsets
  were first drawn as no bar at all; commit bb78775 added a "0" label on every lane that has edges,
  because an invisible zero bar looked the same as a lane with no edges.

## M4: Skew and drift scenarios
- **What:** `drift_offset` in `device/clock.py`; `LaneTiming(skew_cycles, drift_ppb)` and the output
  delay in `device/device.py`; `Scenario` + `SCENARIOS` + `long_commands()` in `scenarios.py`; one
  golden per scenario; `python -m qtwin.analysis.plots --scenario NAME [--out DIR]` writes to out/NAME/.
- **Faults live in the device, not in registers.** `Device(timing=...)` is the hidden truth; the
  driver can only measure its effect on the Scope (M6). Unknown lane names raise.
- **Skew is positive only** (`LaneTiming` raises on < 0); drift may be + or -.
- **Delay after the register:** the register takes the value at the issue cycle; only the Scope edge
  is shifted. SKEW_TRIM is read from the register at issue time (reset 0), so M6 only writes it.
- **Drift is evaluated at the issue cycle** (the device clock when the sequencer applies the command).
  For on-time commands that equals the ideal timestamp, as planned; for a late command it is the
  later cycle, because the output path drifts with real elapsed time (confirmed before M6).
- **Trace in issue order**, not sorted by actual_cycle; a test shows skew_some reorders across lanes.
- **`ideal` keeps M3's 24-command list**, so `tests/golden/ideal.json` is byte-identical. Fault
  scenarios use `long_commands()` (56 commands, markers at 250k..1M cycles; fits the 64-entry FIFO).
- **`mixed` reuses** skew_some's AOM1/GPIO2 skew and drift_some's drift [assumed].
- **SNSPD0 +2 in skew_some is stored but inert** until M7 [assumed].
- **No seed in M4**: there is no random source, so determinism is tested as two identical runs.
  Seeds arrive with bus jitter (M5).
- **Plots output moved** from out/*.png to out/<scenario>/*.png; the old main test was replaced
  because the interface changed.

## M5: Bus model (done after M6, before M7)
- **Skipped, not dropped** (confirmed). The bus stayed instant (0 cycles, no jitter) through M6.
- **Now done before M7** (confirmed 2026-11-11): M7's polling and interrupt drivers need bus cost,
  underrun and FIFO-level data, so M5 must come first. Bus defaults stay ideal (0 latency, 0 jitter),
  so M4 goldens and M6 calibration are unchanged.
- **What:** `Bus(device, bus_latency_cycles, max_jitter_cycles, seed)` with `Bus.writes` log;
  `Device.check_write/schedule_write` (bus port with an in-flight queue); UNDERRUN; tail drop +
  `FIFO.DROPPED`; `Device.fifo_levels/status_events`; `driver/base.py` (`stream`, `margin_cycles`,
  `read_cycle`); `Scenario.bus/stream`, `run() -> RunResult`; scenarios `bus_jitter`, `underrun`,
  `overflow`; plots `fifo_level.png`, `bus_jitter.png`.
- **In-order delivery** (confirmed: real buses keep transaction order):
  `arrival = max(issue + latency + jitter, previous arrival)`. Jitter
  delays a write but never lets it overtake, like AXI writes with one ID. Without this a jittered
  CMD_PUSH word could land in the wrong frame slot. The rule lives in the device's bus port
  (`schedule_write`), because the port owns the queue and reset clears it.
- **Bus arrivals before the sequencer on the same cycle** (confirmed). A frame landing on its own
  timestamp is on time, which matches M2's `ts < now` = late rule and keeps the 0-latency bus
  exactly the old instant bus.
- **Bad accesses raise at issue**, not on arrival (`check_write`): the host hears about a bad address
  in its own call. A bad command target still fails on the 4th word's arrival, since only the full
  frame knows its target.
- **Reset discards writes in flight** (confirmed): the bus port is device state like any other.
- **Margin = lead - (latency + max jitter + CMD_WORDS)**, as planned. The exact boundary is -1 (a frame
  may land on its timestamp cycle); tests cover margins 6, 0 and -1 (identical) and -2, -24 (misses).
- **Streamer timing:** word k goes out at start + k, one write per cycle; `start_i = max(ts_i - lead,
  start_{i-1} + host_gap_cycles, now)`. `host_gap_cycles` is how the underrun scenario makes the host
  slower than the sequencer. `stream` lives in `driver/base.py` so M7's drivers can reuse it.
  `read_cycle` moved there from calibration.
- **Scenario numbers** (confirmed): `bus_jitter` L=20, J=0..30, seed 1, lead 60 (margin 6), 40
  commands 100 cycles apart from cycle 10_000; `underrun` L=10, J=0, lead 100, host_gap 40, 12
  commands 20 cycles apart; `overflow` 70 commands prefilled into the 64-entry FIFO.
- **Underrun definition** (confirmed): while RUN is set, a command committed into an **empty** FIFO after
  its timestamp. Flagged on the commit cycle, the first cycle the device can know. An empty FIFO after
  the last command never triggers it; a late command behind others is LATE only (head-of-line
  blocking); a stopped sequencer can't underrun. The M2 late-command test now also expects UNDERRUN,
  because its scenario is exactly this case.
- **Overflow = tail drop.** Rejected: dropping the oldest (it was accepted first, the host already
  counts on it) and blocking the writer (a register bus has no back-pressure in this model).
  `FifoFullError` is gone; `CommandFifo.push` returns False and the device applies the policy.
- **DROPPED is a register** (RO, saturating, reset clears it), not a device attribute: the driver
  can only see the device through the bus. Rejected: clear-on-read (a debug read would lose counts).
- **FIFO level and status events are instrumentation**, not registers: plots and exact-cycle tests
  need history, which the hardware doesn't keep. A reset doesn't add a level entry [assumed].
- **`run_scenario` stays** as the prefilled, instant-bus reference that the KEY test compares against.
  `run(scenario)` is the full path. The golden test uses `run().edges`, and the 5 M4 goldens are
  byte-identical.
- **Plots:** the FIFO plot's y-axis follows the data; the capacity line appears only when the level
  gets near it. Markers use the reserved status colours, with a text label and an event count.
  The jitter histogram marks the margin-0 delay (lead - CMD_WORDS).
- **Registry-pinned tests updated:** the registry list grew, and "every scenario fits the FIFO" now
  excludes `overflow` by design. The main plots test expects each scenario's own plot set.

## M6: Driver calibration
- **What:** `driver/calibration.py`: `calibrate(bus, scope, wait_until)` -> `CalibrationResult` of per-lane
  `LaneCalibration(skew, trim, residual, drift_ppb, flagged)`; `scope.offsets_at`; `run_scenario(trims=...)`;
  `calibrate_scenario(name)`; `python -m qtwin.analysis.plots --scenario NAME --calibrate`.
- **`wait_until` is passed in** (confirmed): the driver can't import the device to advance time, so the
  test bench hands it `device.run_until`. A `Bus.wait_until` was rejected: it mixes "time passes" into
  the register path.
- **Sequence:** edge at T1 = now + 1_000 (skew), write trims, verify edge at T1 + 1_000 (residual),
  edge at T2 = T1 + 10^6 (drift). Lead of 1_000 cycles confirmed. Residual = offset - max_skew, because trims align to the slowest lane.
- **Drift estimate:** `((off2 - trim) - skew) * 10**9 // span`, integers only. The trim is subtracted
  because the driver wrote it and knows it. Two points, no fit.
- **`DRIFT_THRESHOLD_PPB = 2_000`** (confirmed), flag on `|ppb| > threshold`. It must exceed one quantum
  (1_000 ppb at span 10^6) so one staircase step can't cause a false positive.
- **Calibration edges write back the current value** of AOM PHASE / GPIO LEVEL: a Scope edge with no
  change to the output state.
- **SNSPD lanes excluded** until M7 (no output edge, no SKEW_TRIM).
- **Calibration uses only a scenario's timing**, not its commands; the plot then reruns the commands
  with the found trims.
- **One commit for calibration + harness + tests** instead of the planned three: skew and drift share
  one measurement sequence, and the tests need `calibrate_scenario`.
- **Calibration is idempotent** (confirmed, replaces the "fresh device" assumption): `calibrate()`
  first writes SKEW_TRIM = 0 on all 8 lanes, so the T1 edge reads the raw skew. Without it a second
  run would read skew + old trim (equal on every lane) and write all trims back to 0.

## M7: Polling vs interrupt driver, SNSPD feedback
- **What:** read cost in `Bus.read32` plus `read_count`; FIFO_LOW and `Device.irq_events`;
  `Device.run_until_irq`; `device/snspd.py` (`seeded_clicks`, clicks as a third event source);
  `driver/base.py` (`push_frame`, `unwrap_ts`, `start_stream`, `DriverLog`); `driver/polling.py`;
  `driver/interrupt.py` (`Feedback`, `min_feedback_latency`, `min_watermark`); `DRIVER_SCENARIOS` +
  `run_driver`; `latency_histograms` (`python -m qtwin.analysis.plots --drivers`).
- **Read cost:** a read runs the device for `bus_latency_cycles` and samples on arrival. One-way cost
  and no read jitter [assumed]: adding reads never changes a seed's write-jitter sequence, and the 8
  goldens stay byte-identical. With latency 0 a read doesn't touch time at all: `run_until(now)` would
  run commands due this cycle, which changed an M2 test's result.
- **`set_status` splits:** STATUS gets only bits 0-2 (`STATUS_MASK`), IRQ_STATUS gets every bit.
  `status_events` stays error-only (the M5 tests that pin it are unchanged); `irq_events` records all.
- **FIFO_LOW = edge on the crossing pop** (LEVEL goes from W to W - 1) [assumed]. Rejected: a level
  (re-raising after every pop would run the ISR once per command).
- **`wait_for_irq` is a callback**, like `wait_until`: the driver can't import the device.
  `run_until_irq` steps from event to event (bus arrival, click, FIFO head), so it finds the exact
  rising cycle without ticking.
- **Click model:** uniform integer gaps from a private seeded generator [assumed]; clicks are a hidden
  Device input like the lane faults. On one cycle the order is bus write, click, sequencer [assumed].
  COUNT wraps [assumed]; Scope edge `SNSPDn.COUNT`, no input skew [assumed].
- **The ISR acks first, waits L + J for the posted write to land, then reads COUNT/LAST_TS.** Added
  after planning: reading before the ack lands can clear a newer click's bit. It adds L + J to both
  formulas below (the plan had 3L and no flush). A real driver would instead flush the posted
  write with a read-back (read IRQ_STATUS after the ack: on an in-order bus the read cannot pass
  the write). The model waits L + J because its reads do not wait for writes in flight.
- **Feedback budget:** `feedback_latency_cycles >= irq_latency + L (IRQ_STATUS) + (L + J) (ack flush)
  + 2L (COUNT, LAST_TS) + decide_cycles + (CMD_WORDS - 1) + L + J`, checked in
  `InterruptDriver.__init__`. The decision is `AOM0.ENABLE = COUNT % 2` [assumed].
- **Watermark:** `LOW_WATERMARK >= ceil((irq_latency + 3L + 2J + CMD_WORDS - 1) / cycles_per_command)`.
  `min_watermark(200, 20, 0, 50) = 6`; a test shows 6 holds and 5 underruns.
- **Polling order:** COUNT first (detection latency), then LEVEL, then refill `CAPACITY - LEVEL`. A
  poll whose refill overruns the period delays the next one.
- **Scenarios** [assumed numbers]: `refill` 400 commands every 50 cycles, L = 20, polling 5 000, IRQ
  latency 200, watermark 16; `sparse_clicks` 20 clicks 20 000-60 000 apart (seed 2), polling 1 000,
  IRQ latency 200, feedback 400, decide 20. Separate `DRIVER_SCENARIOS` registry and no goldens:
  determinism is tested by running twice.
- **Percentiles are nearest-rank integers** (`stats.percentile`): p50/p99 are always real latencies.
- **Tests changed**, both because a bus read now takes time, not because the behaviour under test
  changed: `test_bus.py::test_write_lands_exactly_bus_latency_cycles_later` and
  `test_device_commands.py::test_underrun_over_a_slow_bus_is_flagged_on_the_arrival_cycle` check
  device state at a precise cycle over a slow bus, and now use `device.read`, a device-side peek.
