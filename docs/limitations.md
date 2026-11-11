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
- **Instant bus in M1.** `read32`/`write32` take 0 cycles. Bus latency and jitter arrive in M5.
- **No counter wrap.** The 64-bit counter raises instead of wrapping at 2^64 (about 2,339 years at 250 MHz).
- **Identification and sizes are invented:** ID, VERSION, FIFO CAPACITY (64), LOW_WATERMARK reset (16)
  and SNSPD THRESHOLD reset (0) are marked [assumed] in `docs/registers.md`.

## Commands and sequencer
- **Full FIFO raises** `FifoFullError` (M2). Real hardware drops or blocks; M5 adds drop + STATUS.OVERFLOW.
- **Applying a command takes 0 cycles**, and any number of commands can run on the same cycle.
  Real sequencers have a pipeline depth and a limited number of outputs changed per cycle.
- **Every applied write is a Scope edge**, including a write of the value already present.
- **Output timing is a per-lane formula** (M4): actual = issue cycle + skew + SKEW_TRIM +
  drift_offset(issue cycle, drift_ppb). In the `ideal` scenario on-time edges land exactly on the
  commanded cycle.
- **Time moves only through `Device.run_until`**, called by tests. There is no free-running simulation loop.

## Scope and plots
- **The Scope records register writes, not waveforms.** An edge is "this register took this value at
  this cycle". There are no rise times, analog levels, glitches or AOM ramp-up.
- **Offsets are whole cycles.** offset = actual - ideal, an integer number of 4 ns cycles (see Time).
- **Per-channel offset is the earliest edge only.** It is one reading, not an average, so with drift
  it ignores how the offset changes later; `offset_vs_time` shows the full series.
- **All fields of one AOM share one lane** (FREQ_WORD, AMPL, PHASE, ENABLE), as one physical output.
- **SNSPD lanes are empty until M7**: the detectors have no output edges in the model yet.
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
- **SNSPD skew is stored but has no effect** until M7: detectors have no edges and no SKEW_TRIM.
- **The timing diagram can't show small offsets over long runs**: over 10^6 cycles, a 50-cycle
  offset is below one pixel. Use offset_vs_time. A zoomed window is a possible next step.
