# Register map

Single source of truth in code: `src/qtwin/device/registers.py`. This page documents it.
All registers are 32 bits wide and word-aligned. Address = `0x4000_0000` + block offset + register offset.

Values marked [assumed] are model choices, not taken from a real device.

## Blocks

| Block     | Offset          |
|-----------|-----------------|
| SYSTEM    | 0x000           |
| AOM0-3    | 0x100 - 0x400   |
| GPIO0-3   | 0x500 - 0x800   |
| SNSPD0-1  | 0x900 - 0xA00   |
| CLOCK     | 0xB00           |
| FIFO      | 0xC00           |

## Access types

| Type | Read | Write |
|------|------|-------|
| RO   | value | raises `BusError` (strict model; real hardware usually ignores it) |
| RW   | value | stores the value |
| W1C  | value | each bit written as 1 is cleared; bits written as 0 are left alone |
| WO   | raises `BusError` | accepted (write port) |

Every access also raises `BusError` for an unaligned or unmapped address. Writing a value outside
0..2^32-1 raises `ValueError` (no silent masking).

## Registers

| Block | Offset | Name | Access | Reset | Notes |
|-------|--------|------|--------|-------|-------|
| SYSTEM | 0x00 | ID | RO | `0x5154_5749` | ASCII "QTWI" [assumed] |
| SYSTEM | 0x04 | VERSION | RO | `0x0001_0000` | major.minor in upper/lower 16 bits, v1.0 [assumed] |
| SYSTEM | 0x08 | CTRL | RW | 0 | see bit fields |
| SYSTEM | 0x0C | STATUS | W1C | 0 | sticky error flags |
| SYSTEM | 0x10 | IRQ_STATUS | W1C | 0 | pending interrupt events |
| SYSTEM | 0x14 | IRQ_ENABLE | RW | 0 | interrupt mask, same bit positions as IRQ_STATUS |
| CLOCK | 0x00 | FREQ_HZ | RO | 250_000_000 | Hz |
| CLOCK | 0x04 | CYCLE_LO | RO | 0 | low 32 bits of the cycle counter; reading it latches CYCLE_HI |
| CLOCK | 0x08 | CYCLE_HI | RO | 0 | high 32 bits, as latched by the last CYCLE_LO read |
| AOMn | 0x00 | FREQ_WORD | RW | 0 | |
| AOMn | 0x04 | AMPL | RW | 0 | |
| AOMn | 0x08 | PHASE | RW | 0 | |
| AOMn | 0x0C | ENABLE | RW | 0 | |
| AOMn | 0x10 | SKEW_TRIM | RW | 0 | output delay, cycles (positive only) |
| GPIOn | 0x00 | LEVEL | RW | 0 | |
| GPIOn | 0x04 | SKEW_TRIM | RW | 0 | output delay, cycles (positive only) |
| SNSPDn | 0x00 | COUNT | RO | 0 | detector clicks |
| SNSPDn | 0x04 | THRESHOLD | RW | 0 | [assumed] reset 0 |
| SNSPDn | 0x08 | LAST_TS | RO | 0 | low 32 bits of the cycle of the last click [assumed] |
| FIFO | 0x00 | CMD_PUSH | WO | - | command write port; no effect until M2 |
| FIFO | 0x04 | LEVEL | RO | 0 | commands queued |
| FIFO | 0x08 | CAPACITY | RO | 64 | commands [assumed] |
| FIFO | 0x0C | LOW_WATERMARK | RW | 16 | commands [assumed] |

AOMn is at 0x100 + n * 0x100 (n = 0..3), GPIOn at 0x500 + n * 0x100, SNSPDn at 0x900 + n * 0x100.

## Bit fields

**CTRL**

| Bit | Name | Meaning |
|-----|------|---------|
| 0 | RUN | sequencer running (used from M2) |
| 1 | RESET | write 1: reset the whole device (all registers, the clock and the CYCLE_HI shadow). Self-clearing, reads back 0. A RUN bit written together with RESET is discarded: the device comes out of reset stopped. |

**STATUS** (sticky: set by the device, cleared only by a W1C write or CTRL.RESET; reading does not clear)

| Bit | Name | Set when |
|-----|------|----------|
| 0 | UNDERRUN | FIFO ran empty while commands were still expected (M5) |
| 1 | OVERFLOW | command pushed into a full FIFO (M5) |
| 2 | LATE | command timestamp already in the past when it reached the sequencer (M2) |

**IRQ_STATUS / IRQ_ENABLE**

| Bit | Name | Event |
|-----|------|-------|
| 0 | UNDERRUN | same event as STATUS.UNDERRUN |
| 1 | OVERFLOW | same event as STATUS.OVERFLOW |
| 2 | LATE | same event as STATUS.LATE |
| 3 | FIFO_LOW | FIFO level dropped below LOW_WATERMARK (M7) |
| 4 | SNSPD0 | detector 0 click (M7) |
| 5 | SNSPD1 | detector 1 click (M7) |

An error event sets its bit in both STATUS and IRQ_STATUS. They are cleared separately: acknowledging
the interrupt (IRQ_STATUS) and clearing the error flag (STATUS) are two different host actions.

The IRQ line to the host is `(IRQ_STATUS & IRQ_ENABLE) != 0`. It is computed, not stored, so masking
an event lowers the line but leaves the event pending.

## Reading the 64-bit cycle counter

Always read **CYCLE_LO first, then CYCLE_HI**. Reading CYCLE_LO samples the counter once, returns
the low 32 bits and copies the high 32 bits into CYCLE_HI (the shadow). Reading CYCLE_HI returns
the shadow and never re-samples. So the pair is one consistent snapshot, even if the low half
rolls over between the two bus reads.

Reading CYCLE_HI first returns whatever the previous CYCLE_LO read latched: 0 after reset,
otherwise a stale value.

Example: counter = 2^32 - 1. Read LO -> 0xFFFF_FFFF, shadow = 0. The counter ticks to 2^32.
Read HI -> 0. The result is 2^32 - 1, which is correct. Without the latch, HI would read 1 and the
result would be 2^33 - 1, off by 2^32 cycles (about 17 s).
