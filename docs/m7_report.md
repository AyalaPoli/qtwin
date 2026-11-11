# M7 report: polling vs interrupt driver + SNSPD feedback

## Results
Final state: `pytest -q` 652 passed (557 before M7), `mypy src` clean, `ruff check .` clean.
All 8 golden traces are byte-identical to M6.

| Scenario | Driver | STATUS | Bus reads | Commands pushed | Detection latency (cycles) |
|---|---|---|---|---|---|
| refill (400 cmds, 1 per 50 cycles) | polling, 5 000-cycle period | UNDERRUN + LATE | 15 | 264 of 400 by the end, 167 late | - |
| refill | interrupt, watermark 16 | 0 | 11 | 400, all on time | - |
| sparse_clicks (20 clicks) | polling, 1 000-cycle period | 0 | 1 699 | 0 | p50 571, p99 = max 968, min 1 |
| sparse_clicks | interrupt, IRQ latency 200 | 0 | 63 | 20 feedback commands | 220 for every click |

- Feedback: `edge_delay(SNSPD0 -> AOM0)` is exactly 400 cycles for all 20 clicks, also with 0..10
  cycles of write jitter, and across the LAST_TS 32-bit wrap.
- Watermark formula checked at its boundary: `min_watermark(200, 20, 0, 50) = 6`. Watermark 6 runs
  clean, 5 underruns.
- Plot: `python -m qtwin.analysis.plots --drivers` writes `out/drivers/latency_histograms.png`.

## Tests first: failures per commit (before the code -> after)
| Commit | Before the code | After |
|---|---|---|
| 1 Charge bus_latency_cycles per read | 3 failed (of 5 new) | 562 passed; 2 existing tests broke along the way, see below |
| 2 Raise FIFO_LOW at the watermark crossing | 12 failed (all new) | 574 passed |
| 3 Add seeded SNSPD click model | 20 failed (collection error: module missing) | 594 passed |
| 4 Extract push_frame and add unwrap_ts | 11 failed (collection error) | 605 passed |
| 5 Add polling driver | 7 failed (collection error) | 612 passed |
| 6 Add interrupt driver with SNSPD feedback | 14 failed (collection error) | 626 passed |
| 7 Add refill and sparse_clicks driver scenarios | 13 failed (collection error) | 639 passed |
| 8 Add latency histogram plot | 13 new failed (collection error in test_stats.py and test_plots.py) | 652 passed |

## Tests changed, and why
- `tests/test_bus.py::test_write_lands_exactly_bus_latency_cycles_later` and
  `tests/test_device_commands.py::test_underrun_over_a_slow_bus_is_flagged_on_the_arrival_cycle`.
  Both check device state at an exact cycle (109 vs 110, 49 vs 50) by reading over a 10- or
  50-cycle bus. Now that a read takes `bus_latency_cycles`, the read itself moves time past the
  cycle being checked. They now use `device.read(...)`, a device-side peek. The behaviour they
  test is unchanged.
- One more existing test failed in commit 1, `test_late_commit_into_empty_fifo_sets_underrun_and_late`.
  The test was right; the code was wrong: on the instant bus, a read called `run_until(now)` and
  ran a command due on that cycle. Fixed in the code (latency 0 doesn't touch time), test untouched.
- Two of my own new tests had wrong expected values, which I fixed before writing the code they
  test: the FIFO_LOW rearm test (stopped at cycle 2 500 instead of 1 500) and
  `min_feedback_latency` (I added it up to 343 instead of 323).

## Every [assumed] decision and its reason
- **Reads cost one-way latency, sampled on arrival, no read jitter.** The simplest model that charges
  reads, and it keeps every seed's write-jitter sequence (and the M5 goldens) unchanged.
- **A read at latency 0 doesn't advance time.** Keeps the instant bus exactly the M1-M6 bus.
- **FIFO_LOW is an edge on the crossing pop.** A level would run the ISR once per command.
- **Click gaps are uniform integers from a seeded generator.** Integer-only scheduling, repeatable;
  Poisson arrivals would need floats or a lookup table and add nothing to the timing concepts.
- **Same-cycle order: bus write, click, sequencer.** Bus-first was already decided in M5; clicks go
  before the sequencer so a command on a click's cycle sees the click counted.
- **Clicks count while RUN is clear, and reset doesn't replay them.** The detector is an input,
  independent of the sequencer; photons that already arrived are outside the device.
- **COUNT wraps modulo 2^32.** Hosts use differences, which stay correct; DROPPED saturates instead
  because it is read as an absolute "how bad" number.
- **SNSPD Scope edges have no input skew, and THRESHOLD is ignored.** Nothing in M7 needs them;
  listed in limitations.md.
- **ISR acks only the bits it read, then waits L + J before reading COUNT/LAST_TS.** Prevents losing
  a click that arrives between the read and the ack's arrival (posted-write race).
- **Interrupt refill pushes CAPACITY - LOW_WATERMARK commands without reading LEVEL.** At the crossing
  LEVEL < watermark, so it always fits, and it saves a read per refill.
- **Feedback decision `AOM0.ENABLE = COUNT % 2`.** A decision that depends on what was measured,
  simple enough to check: successive clicks switch the AOM on and off.
- **Scenario numbers** (`refill`: 400 x 50 cycles, polling 5 000, IRQ 200, L 20, watermark 16;
  `sparse_clicks`: 20 clicks 20 000-60 000 apart, seed 2, polling 1 000, feedback 400, decide 20).
  Chosen so that the effects are clear: 64 x 50 = 3 200 < 5 000 means slow polling must underrun;
  a watermark of 16 > 6 means interrupts must not; clicks are sparse compared with the poll period.

## Deviations from the approved plan
- **Ack flush added**: both formulas gained L + J (the plan had 3L reads and no flush), and the
  feedback minimum for the scenario is 323 cycles (budget 400).
- **Plot command**: `python -m qtwin.analysis.plots --drivers` -> `out/drivers/latency_histograms.png`
  (the plan named `out/latency_histograms.png` from the plain command). A flag keeps the existing
  per-scenario CLI and its tests unchanged.
- **`start_stream` and `DriverLog`** were added to `driver/base.py`. Both drivers share them.
- **architecture.md layout**: it listed `aom.py` and `gpio.py`, which have never existed (AOM/GPIO are
  registers handled in `device.py`). The layout line now lists the real files. CLAUDE.md still names
  them; I have not edited CLAUDE.md.

## Unfinished / next steps
- Polling feedback (acting on clicks from the polling loop) is not implemented; only detection.
- The M7 scenarios have no golden traces; determinism is tested by running each twice.
- 20 clicks make p99 equal max; a longer click list would show the polling tail better.
- Feedback commands and a pre-queued stream in the same FIFO would block each other (in-order FIFO);
  a priority or second queue is a possible next step.
- Interrupt latency is a constant here; a model of variable CPU latency (cache, other IRQs) would
  give the interrupt histogram a tail.
