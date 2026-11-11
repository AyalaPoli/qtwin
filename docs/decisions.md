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
- **`scenarios.py` holds only `ideal_commands()` and `run_scenario()`** for now [assumed]; the Scenario
  dataclass and registry are M4. `run_scenario` goes through Bus/CMD_PUSH, the real path.
- **Plots use `Figure` + explicit Agg canvas, not pyplot**: no global state, no GUI backend.
  Offset vs time is small multiples (one panel per lane, shared axes) instead of 10 colors on one chart.
  `offset_bars` takes a label -> trace mapping, so M6's "after calibration" is a second entry.
- **Deferred:** FIFO fill level (M5), bus-jitter histogram (M5) and polling vs interrupt latency
  histograms (M7). Their data doesn't exist yet. Plotting them now would mean inventing it.

