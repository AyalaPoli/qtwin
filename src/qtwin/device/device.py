"""The device: register storage, address decoding and access rules.

Two kinds of access:
- Bus side (read/write): what the host can do. Every access rule (RO, RW, W1C, WO) applies here.
- Device side (set_status): what the hardware itself does, e.g. flag an underrun. It bypasses the
  bus rules, the same way logic inside an FPGA drives a status bit directly.
"""

from qtwin.device.clock import Clock
from qtwin.device.registers import (
    BY_ADDRESS,
    BY_NAME,
    REG_MASK,
    REGISTERS,
    Access,
    BusError,
    Register,
)


class Device:
    """Register-level model of the control box. Starts in its reset state."""

    def __init__(self) -> None:
        self.clock = Clock()
        # Current value of every register, keyed by absolute byte address.
        self._values: dict[int, int] = {reg.address: reg.reset for reg in REGISTERS}

    # --- Bus side ---

    def read(self, address: int) -> int:
        """Return the 32-bit value at byte `address`. Raises BusError for a bad address or WO."""
        reg = self._decode(address)
        if reg.access is Access.WO:
            raise BusError(f"{reg.name} is write-only")
        return self._values[address]

    def write(self, address: int, value: int) -> None:
        """Write a 32-bit `value` to byte `address`, following the register's access type.

        Raises ValueError if value is outside 0..2^32-1 (no silent masking), BusError for a bad
        address or a write to an RO register.
        """
        if not 0 <= value <= REG_MASK:
            raise ValueError(f"value must fit in 32 bits, got {value}")
        reg = self._decode(address)
        if reg.access is Access.RO:
            raise BusError(f"{reg.name} is read-only")
        if reg.access is Access.RW:
            self._values[address] = value
        elif reg.access is Access.W1C:
            # Clearing with a mask means the host can't accidentally clear a bit the device set
            # after the host last read the register: it only clears bits it names explicitly.
            self._values[address] &= ~value
        else:
            # WO: FIFO.CMD_PUSH. Accepted with no effect until the FIFO exists (M2).
            pass

    # --- Device side ---

    def set_status(self, mask: int) -> None:
        """Hardware raises the event bits in `mask` in both STATUS and IRQ_STATUS.

        The bits are sticky: they stay set until the host clears them with a W1C write.
        """
        for name in ("SYSTEM.STATUS", "SYSTEM.IRQ_STATUS"):
            self._values[BY_NAME[name].address] |= mask

    # --- Internals ---

    def _decode(self, address: int) -> Register:
        # Unaligned first: a 32-bit bus can't address a register starting mid-word.
        if address % 4 != 0:
            raise BusError(f"unaligned address {address:#x}")
        reg = BY_ADDRESS.get(address)
        if reg is None:
            raise BusError(f"unmapped address {address:#x}")
        return reg
