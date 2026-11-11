# Architecture

qtwin is a simplified software model of an FPGA control box and its host driver.
It is not RTL and not cycle-exact silicon (see `limitations.md`).

```
+--------+   Bus.read32 / write32    +-------------------------------------------------+
| Driver | ------------------------> | Device                                          |
|        |   bus_latency_cycles      |   Clock (250 MHz, integer cycles)               |
|        |   (+ jitter on writes)    |   Register map (RO / RW / W1C)                  |
|        | <------- IRQ line ------- |   Command FIFO --> Sequencer --> 4 AOM          |
+--------+                           |                                  4 GPIO         |
    |                                |                                  2 SNSPD        |
    | read-only                      +-------------------------------------------------+
    v                                                       | every output edge
+--------+                                                  v
| Scope  | <------------------------------------------------+
+--------+   passive recorder; tests, calibration and plots read it
```

Time passes only through callbacks the test bench hands the driver: `wait_until(cycle)`
(`Device.run_until`) and, for the interrupt driver, `wait_for_irq(limit)` (`Device.run_until_irq`,
the host's interrupt input). A bus read blocks the host for `bus_latency_cycles` (M7).
SNSPD clicks are inputs: they update COUNT/LAST_TS, raise IRQ_STATUS and appear on the Scope.

Boundary rule: the Driver reaches the Device only through `Bus.read32/write32`. Driver code may
import `bus`, `registers` and the read-only `scope` API, never `qtwin.device`.
`tests/test_architecture.py` enforces this with a static (ast) check.

## Directory layout
```
src/qtwin/
  timebase.py      seconds <-> integer cycles, half-even rounding        (M0)
  device/          clock, registers, fifo, sequencer, snspd, device      (M1, M2, M7)
  bus.py           read32/write32 with latency, seeded write jitter       (M5, M7)
  driver/          base (stream, helpers), polling, interrupt, calib.     (M5-M7)
  scope.py         virtual oscilloscope / logic analyzer                  (M2, M3)
  scenarios.py     Scenario registries: faults, bus, driver experiments    (M4, M5, M7)
  analysis/        stats and matplotlib plots (PNGs to out/)              (M3)
cpp/ringbuffer/    lock-free SPSC ring buffer + benchmark                 (M8)
tests/  tests/golden/  docs/  out/
```
