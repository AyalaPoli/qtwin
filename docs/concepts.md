# Real-time concepts, one milestone at a time

## M0: Integer time, rounding once at the edge
1. Hardware sequencers count clock ticks; they have no notion of seconds or fractions of a tick.
2. Floats can't represent most decimal times exactly, and their errors add up as events are scheduled.
3. qtwin therefore keeps every timestamp as an integer number of 4 ns cycles (250 MHz).
4. Seconds are converted exactly once, at the API edge, with one stated rule: round half to even.
5. Every later comparison of times (late commands, skew, drift) is exact integer arithmetic, so results are reproducible.

## M1: Memory-mapped registers
1. The host controls the device only by reading and writing 32-bit words at fixed addresses; each address is a register with a stated access type (RO, RW, W1C, WO).
2. Status bits are sticky: the hardware sets them when something goes wrong, and they stay set until software clears them, so a brief error can't be missed between two reads.
3. W1C (write-1-to-clear) lets software clear exactly the bits it has handled; a bit that the hardware sets in the meantime survives, which a plain read-modify-write would erase.
4. A 64-bit counter takes two 32-bit reads, and it keeps counting in between. Reading LO latches HI into a shadow, so the pair is one snapshot even across a rollover.
5. The interrupt line is pending AND enabled (`IRQ_STATUS & IRQ_ENABLE`): masking hides an event from the CPU without losing it.

## M2: Timestamped commands and event-driven sequencing
1. A command carries the cycle at which it must happen, so WHEN software sends it is decoupled from WHEN the output changes: software only has to be early, not punctual.
2. The FIFO is strictly first-in first-out and the sequencer only looks at the head, so commands with the same timestamp always run in push order, with no tie-break rule needed.
3. The simulation is event-driven: the clock jumps straight to the next command's timestamp, so a long run with few commands costs a few steps, not one step per cycle.
4. A late command (timestamp already passed) is a missed deadline. The device runs it at once and sets a sticky LATE flag, so the miss is never silent.
5. The output trace depends only on the commands and their timestamps, so the same input always gives the identical trace.

## M3: Observability: commanded vs actual
1. The Scope records two times per edge: the cycle the command asked for (ideal) and the cycle the output really changed (actual). You can only debug timing you can see.
2. offset = actual - ideal. It is the one number that skew, drift, late commands and (later) latency all reduce to.
3. A constant offset over time is static skew (calibrate once); an offset that grows with time is drift (needs tracking). Plotting offset vs time with a fitted line tells them apart: intercept = skew, slope = drift.
4. Measurements stay integer cycles; floats appear only in the analysis fit, never in scheduling, so analysis can never change behaviour.
5. A golden trace freezes the expected output of a known scenario. Any change in timing becomes a failing test and a reviewable diff, and regenerating it is a deliberate act.

## M4: Static skew vs drift (fault injection)
1. Skew is a fixed delay on one output path (cable, buffer, delay line): every edge on that lane lands the same number of cycles late, so it can be measured once and calibrated away.
2. Drift is a small frequency error (parts per billion): the offset grows with elapsed time, as a staircase of whole-cycle slips (+50 ppm = one cycle every 20 000 cycles), so a one-time calibration goes stale and must be tracked.
3. The sequencer still acts at the commanded cycle; the delay lives after the register, in the output path. Scheduling and output timing are separate, as in hardware.
4. Hardware delay lines can only add delay, so channels are aligned to the slowest one (SKEW_TRIM = max_skew - skew), never by moving an edge earlier.
5. Faults are injected on purpose, with exact known values, so a test can check that a measurement recovers what was put in. Hand-computed edges check correctness; golden traces only detect change.

## M5: Buffering absorbs jitter (until it doesn't)
1. A bus write doesn't land when the host makes it: it lands latency + jitter cycles later. The FIFO decouples the two clocks: the host writes early, the sequencer plays exactly on time.
2. The cost of jitter is zero as long as every command arrives before its timestamp. That slack is the margin: lead - (latency + max jitter + 4 words). Inside the margin the output trace is bit-identical to an instant bus, whatever the seed.
3. Below the margin the failure is sudden, not gradual: commands go late, and if the FIFO was already empty the sequencer was starved (UNDERRUN). A host that is slower on average than the sequencer always ends there, however big the lead.
4. A full FIFO must have a policy. This model drops the new command (tail drop), counts it in FIFO.DROPPED and keeps every command already accepted. Blocking the writer would be the other choice.
5. Error flags are sticky and stamped with the exact cycle, so a fault that lasted one cycle is still visible when the host looks much later. Every random source has a seed, so a failing run can be replayed exactly.

## M6: Closed-loop calibration (measure, correct, verify)
1. Calibration is a closed loop: send a known stimulus (one edge at the same cycle on every lane), measure the response on the Scope, write a correction (SKEW_TRIM), then measure again to prove the residual is 0.
2. The driver does it only through registers and an external measurement, like a real host with an oscilloscope; it never peeks at the device's hidden fault values.
3. Positive-only delay lines mean "aligned" is not "on time": every lane ends up as late as the slowest one, and they all land together.
4. Two readings far apart separate the two faults: the early reading is the intercept (skew), the growth between them is the slope (drift). Measuring skew early keeps drift from leaking into it.
5. Resolution is a limit of the measurement, not the math: the Scope sees whole cycles, so drift below one cycle per span (1 000 ppb over 10^6 cycles) reads as 0. A longer span finds smaller drift, at the cost of calibration time.
