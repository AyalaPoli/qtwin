# qtwin: register-level model of an FPGA control box + host driver

## Purpose
It must implement real-time and hardware concepts and be a working code
sample I can explain line by line. Full design and milestones: `docs/qtwin_plan.md` (read it before planning).
It is a deliberately simplified MODEL: not RTL, not cycle-exact silicon, crude physics.
Never claim otherwise in code, docs or README. Known simplifications go in `docs/limitations.md`.

## Architecture
Driver --(Bus.read32/write32, latency + jitter)--> Device
Device = Clock + Register map + Command FIFO -> Sequencer -> {4 AOM, 4 GPIO, 2 SNSPD} + IRQ line.
Scope = passive, read-only recorder of every output edge. Tests, calibration and plots read the Scope.

Directory layout (DECIDED; do NOT use sim/ and hal/):
```
src/qtwin/
  timebase.py            seconds <-> integer cycles, explicit rounding (half-even)
  device/                clock.py registers.py fifo.py sequencer.py snspd.py device.py (AOM/GPIO registers live in device.py)
  bus.py                 read32/write32 with named bus_latency_cycles and seeded jitter
  driver/                base.py polling.py interrupt.py calibration.py
  scope.py               virtual oscilloscope / logic analyzer + measurement helpers
  scenarios.py           Scenario dataclass + registry
  analysis/              stats.py plots.py (matplotlib, Agg backend, PNGs to out/)
cpp/ringbuffer/          lock-free SPSC ring buffer + benchmark (last milestone)
tests/  tests/golden/  docs/ (registers.md concepts.md decisions.md limitations.md architecture.md)  out/
```

## Hard rules
1. Time is simulated INTEGER clock cycles (250 MHz, 4 ns/cycle). Never wall-clock. No floats in scheduling.
   Seconds -> cycles only at the API edge (`timebase.py`), rounding rule stated (half-even).
2. Deterministic: every random source takes a seed; same seed => identical Scope trace.
3. The Driver sees the Device only through `Bus.read32/write32`. Driver code may import `bus`, `registers`
   and the read-only `scope` API, never `qtwin.device`. Enforce with an automatic ast-based test.
4. Registers are defined once in `device/registers.py`, documented in `docs/registers.md`.
5. Latencies are named parameters (`bus_latency_cycles`, `irq_latency_cycles`, `feedback_latency_cycles`),
   never hidden constants.
6. Dependencies approved: matplotlib (runtime), pytest, mypy, ruff (dev). Anything else: ask first.
7. Tests first. Run them. Never edit a test to make it pass unless you explain why the test was wrong.

## Decisions already made (do not re-ask)
- Skew/Scope resolution: whole 4 ns cycles everywhere. Record in `limitations.md` that real skew is sub-ns.
- Skew compensation: device `SKEW_TRIM` registers, positive-only delay lines. Driver aligns every channel
  to the slowest: `SKEW_TRIM_i = max_skew - skew_i`. (You cannot delay backwards.) Not by shifting timestamps.
- Drift: integer parts-per-billion per component, `drift_offset(n, ppb)` truncates toward zero
  (symmetric for +/-), actual = n + skew_cycles + drift_offset(n, drift_ppb). Drift is DETECTED and
  reported in M6 (continuous compensation is a "next step").
- Write to a read-only register: raises an error in the model (strict). Document it.
- The api/compiler layer and camera/atom-loss model from the old design are dropped. Optional thin
  "experiment -> Command list" builder only after M7.

## Workflow
- Plan mode before each milestone; wait for my approval. One milestone at a time.
- One small commit per logical change, imperative messages.
- After each milestone: 5 plain-language lines in `docs/concepts.md` (the real-time concept it shows and why)
  and a short entry in `docs/decisions.md`.
- I must be able to explain every line. Prefer simple code. Comment WHY, not what.
  Docstrings state units (cycles, ns, ppm/ppb) for every numeric parameter.
- When you assume something, mark it [assumed] and tell me.

## Commands (Windows PowerShell)
- Environment: `py -3.13 -m venv .venv` (system `python` on PATH is 3.6, too old). Activate: `.venv\Scripts\Activate.ps1`
- Tests: `pytest -q`   Types: `mypy src`   Lint: `ruff check . ; ruff format .`
- Plots: `python -m qtwin.analysis.plots`
- C++: `cmake -S cpp/ringbuffer -B build ; cmake --build build --config Release`
  (binary must land at `build/ringbuffer_bench` with any generator)

## Out of scope (mention as next steps)
Real RISC-V/RTL, kernel drivers, TCP, web UI, cloud, GPU, detailed atomic physics.
