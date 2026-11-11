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
