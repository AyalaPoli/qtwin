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

## Drift
- Drift is integer parts-per-billion, truncated toward zero, so it appears as whole-cycle slips.
  It is detected and reported, not continuously compensated.
