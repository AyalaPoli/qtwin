"""Register map: single source of truth for every device register (see docs/registers.md).

This module is pure data and imports only the standard library. The driver is allowed to import it,
like a real driver includes the chip's register header, but it holds no device state.

All registers are 32 bits wide. Address = BASE_ADDRESS + block offset + register offset (bytes).
"""

from dataclasses import dataclass
from enum import Enum

BASE_ADDRESS: int = 0x4000_0000
"""Byte address where the device's register space starts."""

BLOCK_SIZE: int = 0x100
"""Bytes of address space per component block."""

REG_MASK: int = 0xFFFF_FFFF
"""Largest value a 32-bit register can hold."""


class Access(Enum):
    """How the bus may access a register."""

    RO = "RO"  # read-only: a write raises (strict model; see docs/limitations.md)
    RW = "RW"  # read/write
    W1C = "W1C"  # read; writing a 1 to a bit clears that bit, writing 0 leaves it
    WO = "WO"  # write-only port (FIFO.CMD_PUSH): a read raises


class BusError(Exception):
    """An access the device rejects: unmapped or unaligned address, or wrong access type."""


@dataclass(frozen=True)
class Register:
    """One 32-bit register.

    name: "BLOCK.REG", e.g. "AOM1.FREQ_WORD".
    block: block name, e.g. "AOM1".
    address: absolute byte address.
    access: allowed bus access.
    reset: value after power-on or CTRL.RESET.
    """

    name: str
    block: str
    address: int
    access: Access
    reset: int


# --- Identification values [assumed] ---
DEVICE_ID: int = 0x5154_5749  # ASCII "QTWI"
DEVICE_VERSION: int = 0x0001_0000  # major 1, minor 0 (upper/lower 16 bits)

# --- Fixed hardware parameters ---
CLOCK_FREQ_HZ: int = 250_000_000  # must equal timebase.CLOCK_HZ (checked by a test)
FIFO_CAPACITY: int = 64  # commands [assumed]
FIFO_LOW_WATERMARK_RESET: int = 16  # commands [assumed]

# --- Bit fields ---
CTRL_RUN: int = 1 << 0
CTRL_RESET: int = 1 << 1  # self-clearing: writing 1 resets the device, the bit reads back 0

# STATUS bits are sticky: the device sets them, only a W1C write or CTRL.RESET clears them.
STATUS_UNDERRUN: int = 1 << 0
STATUS_OVERFLOW: int = 1 << 1
STATUS_LATE: int = 1 << 2

# IRQ_STATUS uses the same positions for the three error events, plus interrupt-only events.
IRQ_UNDERRUN: int = STATUS_UNDERRUN
IRQ_OVERFLOW: int = STATUS_OVERFLOW
IRQ_LATE: int = STATUS_LATE
IRQ_FIFO_LOW: int = 1 << 3
IRQ_SNSPD0: int = 1 << 4
IRQ_SNSPD1: int = 1 << 5

# --- Layout ---
BLOCKS: dict[str, int] = {
    "SYSTEM": 0x000,
    **{f"AOM{i}": 0x100 + i * BLOCK_SIZE for i in range(4)},
    **{f"GPIO{i}": 0x500 + i * BLOCK_SIZE for i in range(4)},
    **{f"SNSPD{i}": 0x900 + i * BLOCK_SIZE for i in range(2)},
    "CLOCK": 0xB00,
    "FIFO": 0xC00,
}
"""Block name -> byte offset from BASE_ADDRESS."""

# Register layout per kind of block: (register name, offset within block in bytes, access, reset).
_Layout = list[tuple[str, int, Access, int]]

_SYSTEM: _Layout = [
    ("ID", 0x00, Access.RO, DEVICE_ID),
    ("VERSION", 0x04, Access.RO, DEVICE_VERSION),
    ("CTRL", 0x08, Access.RW, 0),
    ("STATUS", 0x0C, Access.W1C, 0),
    ("IRQ_STATUS", 0x10, Access.W1C, 0),
    ("IRQ_ENABLE", 0x14, Access.RW, 0),
]
_CLOCK: _Layout = [
    ("FREQ_HZ", 0x00, Access.RO, CLOCK_FREQ_HZ),
    ("CYCLE_LO", 0x04, Access.RO, 0),  # reading it latches the high half; see device.py
    ("CYCLE_HI", 0x08, Access.RO, 0),
]
_AOM: _Layout = [
    ("FREQ_WORD", 0x00, Access.RW, 0),
    ("AMPL", 0x04, Access.RW, 0),
    ("PHASE", 0x08, Access.RW, 0),
    ("ENABLE", 0x0C, Access.RW, 0),
    ("SKEW_TRIM", 0x10, Access.RW, 0),  # delay in cycles
]
_GPIO: _Layout = [
    ("LEVEL", 0x00, Access.RW, 0),
    ("SKEW_TRIM", 0x04, Access.RW, 0),  # delay in cycles
]
_SNSPD: _Layout = [
    ("COUNT", 0x00, Access.RO, 0),
    ("THRESHOLD", 0x04, Access.RW, 0),  # [assumed] reset 0
    ("LAST_TS", 0x08, Access.RO, 0),  # low 32 bits of the cycle of the last click [assumed]
]
_FIFO: _Layout = [
    ("CMD_PUSH", 0x00, Access.WO, 0),
    ("LEVEL", 0x04, Access.RO, 0),  # commands currently queued
    ("CAPACITY", 0x08, Access.RO, FIFO_CAPACITY),
    ("LOW_WATERMARK", 0x0C, Access.RW, FIFO_LOW_WATERMARK_RESET),
]


def _layout_for(block: str) -> _Layout:
    # Strip the trailing channel digit: "AOM2" -> "AOM".
    kind = block.rstrip("0123456789")
    layouts = {
        "SYSTEM": _SYSTEM,
        "CLOCK": _CLOCK,
        "AOM": _AOM,
        "GPIO": _GPIO,
        "SNSPD": _SNSPD,
        "FIFO": _FIFO,
    }
    return layouts[kind]


REGISTERS: tuple[Register, ...] = tuple(
    Register(
        name=f"{block}.{reg_name}",
        block=block,
        address=BASE_ADDRESS + block_offset + reg_offset,
        access=access,
        reset=reset,
    )
    for block, block_offset in BLOCKS.items()
    for reg_name, reg_offset, access, reset in _layout_for(block)
)
"""Every register, ordered by address."""

BY_NAME: dict[str, Register] = {reg.name: reg for reg in REGISTERS}
BY_ADDRESS: dict[int, Register] = {reg.address: reg for reg in REGISTERS}


def addr(name: str) -> int:
    """Absolute byte address of register `name` ("BLOCK.REG"). Raises KeyError if unknown."""
    return BY_NAME[name].address
