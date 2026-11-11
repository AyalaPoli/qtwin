# Architecture

qtwin is a simplified software model of an FPGA control box and its host driver.
It is not RTL and not cycle-exact silicon (see `limitations.md`).

```
+--------+   Bus.read32 / write32    +-------------------------------------------------+
| Driver | ------------------------> | Device                                          |
|        |   bus_latency_cycles      |   Clock (250 MHz, integer cycles)               |
|        |   + seeded jitter         |   Register map (RO / RW / W1C)                  |
|        | <------- IRQ line ------- |   Command FIFO --> Sequencer --> 4 AOM          |
+--------+                           |                                  4 GPIO         |
    |                                |                                  2 SNSPD        |
    | read-only                      +-------------------------------------------------+
    v                                                       | every output edge
+--------+                                                  v
| Scope  | <------------------------------------------------+
+--------+   passive recorder; tests, calibration and plots read it
```

Boundary rule: the Driver reaches the Device only through `Bus.read32/write32`. Driver code may
import `bus`, `registers` and the read-only `scope` API, never `qtwin.device`.
`tests/test_architecture.py` enforces this with a static (ast) check.

## Directory layout
```
src/qtwin/
  timebase.py      seconds <-> integer cycles, half-even rounding        (M0)
  device/          clock, registers, fifo, sequencer, aom, gpio, snspd   (M1, M2)
  bus.py           read32/write32 with latency and seeded jitter          (M5)
  driver/          base, polling, interrupt, calibration                  (M6, M7)
  scope.py         virtual oscilloscope / logic analyzer                  (M2, M3)
  scenarios.py     Scenario dataclass + registry                          (M4)
  analysis/        stats and matplotlib plots (PNGs to out/)              (M3)
cpp/ringbuffer/    lock-free SPSC ring buffer + benchmark                 (M8)
tests/  tests/golden/  docs/  out/
```
