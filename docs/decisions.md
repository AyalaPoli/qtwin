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
