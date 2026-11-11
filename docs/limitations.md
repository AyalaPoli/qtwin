# Known limitations and simplifications

qtwin is a deliberately simplified MODEL. It is not RTL, not cycle-exact silicon behaviour, and the
physics is crude. It exists to show real-time and hardware concepts, not to predict real hardware.

## Time
- **Whole-cycle resolution.** Time, skew and Scope measurements are whole 4 ns cycles (250 MHz).
  Real channel-to-channel skew is often sub-nanosecond. The model can't represent it, and anything
  below one cycle is invisible to calibration.
- **Seconds are read as decimals.** `timebase.seconds_to_cycles` uses the shortest decimal form of the
  float (`str(seconds)`) and does exact rational arithmetic, so `6e-9` is treated as exactly 6 ns.
  A float computed by arithmetic (not typed) is still interpreted through its shortest repr.
- Simulated time is a virtual clock. No wall-clock time is used anywhere.

## Registers
- **Strict read-only registers.** Writing to an RO register raises an error. Real hardware usually
  ignores such writes silently. The model raises so that driver bugs show up immediately in tests.
  The same strictness applies to unaligned/unmapped addresses, reads of the write-only CMD_PUSH,
  and values outside 32 bits.
- **Bus timing** (M5, M7). Writes land after `bus_latency_cycles` plus uniform 0..max jitter.
  A read blocks the host for `bus_latency_cycles` and samples the register on arrival; the
  response path is free and reads have no jitter. A read does not wait for writes still in
  flight, so it can miss a write that lands later. M6 calibration uses the default instant bus.
- **Jitter is uniform and independent per write.** Real bus jitter is bursty and correlated
  (arbitration, refresh, other masters). No bandwidth limit: any number of writes can be in flight.
- **One write per cycle is the streamer's rule, not the bus's.** The bus itself accepts any number
  of writes per cycle.
- **No counter wrap.** The 64-bit counter raises instead of wrapping at 2^64 (about 2,339 years at 250 MHz).
- **Identification and sizes are invented:** ID, VERSION, FIFO CAPACITY (64), LOW_WATERMARK reset (16)
  and SNSPD THRESHOLD reset (0) are marked [assumed] in `docs/registers.md`.

## Commands and sequencer
- **Full FIFO drops the new command** (M5), sets OVERFLOW and counts it in FIFO.DROPPED. No
  back-pressure: the host learns about drops only by reading STATUS or DROPPED.
- **UNDERRUN is detected on arrival**, when the late command commits into the empty FIFO, not at
  the cycle the timestamp passed: the device can't know a command is missing until it arrives.
  A command that never arrives is never reported.
- **Applying a command takes 0 cycles**, and any number of commands can run on the same cycle.
  Real sequencers have a pipeline depth and a limited number of outputs changed per cycle.
- **Every applied write is a Scope edge**, including a write of the value already present.
- **Output timing is a per-lane formula** (M4): actual = issue cycle + skew + SKEW_TRIM +
  drift_offset(issue cycle, drift_ppb). In the `ideal` scenario on-time edges land exactly on the
  commanded cycle.
- **Time moves only through `Device.run_until`**, called by tests, or by the driver through the
  `wait_until` callback the test bench passes in (M6). There is no free-running simulation loop.

## Scope and plots
- **The Scope records register writes, not waveforms.** An edge is "this register took this value at
  this cycle". There are no rise times, analog levels, glitches or AOM ramp-up.
- **Offsets are whole cycles.** offset = actual - ideal, an integer number of 4 ns cycles (see Time).
- **Per-channel offset is the earliest edge only.** It is one reading, not an average, so with drift
  it ignores how the offset changes later; `offset_vs_time` shows the full series.
- **All fields of one AOM share one lane** (FREQ_WORD, AMPL, PHASE, ENABLE), as one physical output.
- **SNSPD edges are clicks** (M7): one edge per click on `SNSPDn.COUNT`, ideal = actual = click cycle.
- **The drift fit is plain least squares** on a staircase of whole-cycle slips, so the fitted ppb is
  approximate (a hand-made 50 000 ppb trace fitted to ~49 300 ppb). Floats are used only there.

## Skew and drift (M4)
- Drift is integer parts-per-billion, truncated toward zero, so it appears as whole-cycle slips.
  It is detected and reported, not continuously compensated.
- **Drift is a formula per edge, not a second clock.** Each lane has no oscillator of its own; its
  error is computed from the device cycle count when an edge is issued. There is no phase noise and
  no jitter on edges: every fault is exact and repeatable.
- **Skew and drift are constant for a run.** Real skew changes with temperature, drift with ageing.
- **Skew is positive only** (a delay line can't advance an edge). Fault values are invented
  ([assumed] for `mixed`, which reuses the skew_some and drift_some values).
- **Changing SKEW_TRIM mid-run** affects only edges issued afterwards, and lowering it could put a
  later edge of a lane before an earlier one. The model does not prevent that.
- **Edges can be recorded "in the future".** The edge is logged when issued, with actual_cycle
  ahead of the device clock by the lane delay. The trace stays in issue order, not actual order.
- **SNSPD skew is stored but has no effect**, even in M7: a click is stamped on the cycle it happens.
- **The timing diagram can't show small offsets over long runs**: over 10^6 cycles, a 50-cycle
  offset is below one pixel. Use offset_vs_time. A zoomed window is a possible next step.

## Calibration (M6)
- **Drift below one cycle per span is undetectable.** The Scope sees whole cycles, so with T2 - T1 =
  10^6 cycles anything under 1 000 ppb reads as 0 (a 900 ppb lane is asserted to read 0). Estimates
  are +-1 cycle, i.e. +-1 000 ppb.
- **Two points, no fit.** Drift comes from one edge at T1 and one at T2. No averaging, which is
  enough here only because the model has no edge jitter.
- **Skew reading includes early drift:** the T1 edge carries trunc(T1 * ppb / 10^9) cycles of drift.
  It is 0 while T1 < 10^9 / ppb (33 333 cycles at 30 ppm); calibration uses T1 = 1 000.
- **Drift is detected, not compensated.** Trims fix only skew; a drifting lane goes out of alignment
  again. Continuous compensation is a next step.
- **SNSPD lanes are not calibrated**: they are inputs with no SKEW_TRIM.
- **Calibration is repeatable, but assumes an idle device.** It clears every SKEW_TRIM first, so a
  second run finds the same trims. Its edges still share the FIFO and the Scope with anything else
  queued, so nothing else should be running.
- **A late re-run reads drift as skew.** On a drifting lane, a calibration started at a large cycle
  count measures skew + the drift accumulated since reset (e.g. +50 ppm at cycle 10^6 adds 50 cycles).
  Calibrate soon after reset, or treat a drifting lane's skew as unreliable.

## Polling, interrupts and feedback (M7)
- **Clicks are not photons.** Gaps between clicks are uniform integers from a seeded generator, not
  Poisson arrivals. No dead time beyond "one click per cycle per detector", no dark counts, no
  efficiency; THRESHOLD is stored but ignored. Clicks count while RUN is clear, and a reset does
  not replay clicks already delivered.
- **COUNT wraps at 2^32 and LAST_TS every 2^32 cycles (17.18 s).** Drivers use COUNT differences
  modulo 2^32 and `unwrap_ts`; a click 2^32 cycles or more before the ISR reads LAST_TS is
  misplaced by multiples of 2^32. Only the last click's timestamp is kept: when several clicks
  arrive before the ISR, the earlier timestamps are lost (each detection uses the ISR's time).
- **FIFO_LOW is an edge** on the pop that crosses below the watermark. A FIFO that never fills above
  the watermark never interrupts, so the interrupt driver fills it before RUN.
- **The host is one thread with no preemption.** The ISR runs to completion; an IRQ during the ISR
  waits until it returns. Driver code takes 0 cycles except for bus accesses, frames (one word per
  cycle), the IRQ latency and `decide_cycles`. A real CPU adds cache misses, scheduling and other
  interrupts: real interrupt latency is not constant.
- **irq_latency_cycles is one fixed number.** The IRQ line rises on the event cycle with no
  synchronisation delay.
- **The interrupt refill pushes CAPACITY - LOW_WATERMARK commands without reading LEVEL.** Safe
  because LEVEL < watermark at the crossing; with a watermark of 0 it never fires.
- **The polling refill trusts LEVEL**, which does not count frames still in flight. That is safe
  only while the poll period is much longer than bus latency + jitter (true in every scenario).
- **Feedback commands share the FIFO with the stream.** The FIFO is in order, so a feedback command
  queued behind later-stamped stream commands would wait (head-of-line blocking) and go late. The
  scenarios keep them apart: `refill` has no clicks, `sparse_clicks` has no stream.
- **Polling feedback is not modelled.** Only the interrupt driver acts on clicks.
- **Latency means "the host knows"**: when the read that showed the click returns, not when an
  action happens. Action timing is measured separately on the Scope (`edge_delay`).
- **20 clicks per run** make p99 equal to max. The histogram shows the shape, not a precise tail.
