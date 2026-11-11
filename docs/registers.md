# Register map

Single source of truth in code: `src/qtwin/device/registers.py` (added in M1).
This page documents it. All registers are 32 bits wide.

Base address: `0x4000_0000`. Each component has one `0x100`-byte block.

| Block     | Offset          |
|-----------|-----------------|
| SYSTEM    | 0x000           |
| AOM0-3    | 0x100 - 0x400   |
| GPIO0-3   | 0x500 - 0x800   |
| SNSPD0-1  | 0x900 - 0xA00   |
| CLOCK     | 0xB00           |
| FIFO      | 0xC00           |

Access types: RO (read-only; a write raises an error in this model), RW, W1C (write 1 to clear).

Per-register offsets, reset values and bit fields: to be filled in M1.
