"""Reading the 64-bit cycle counter through two 32-bit registers.

The host needs two bus reads, and the counter keeps running between them. Reading CYCLE_LO copies
the high half into a shadow register; CYCLE_HI returns that shadow, so the pair is one snapshot.
"""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device

LO = r.addr("CLOCK.CYCLE_LO")
HI = r.addr("CLOCK.CYCLE_HI")


@pytest.fixture
def device() -> Device:
    return Device()


@pytest.fixture
def bus(device: Device) -> Bus:
    return Bus(device)


def read_cycles(bus: Bus) -> int:
    """The documented read order: LO first (latches), then HI."""
    lo = bus.read32(LO)
    hi = bus.read32(HI)
    return (hi << 32) | lo


def test_reads_current_counter(device: Device, bus: Bus) -> None:
    device.clock.advance(1_000)
    assert read_cycles(bus) == 1_000


def test_reads_full_64_bit_value(device: Device, bus: Bus) -> None:
    device.clock.advance(0x1234_5678_9ABC_DEF0)
    assert read_cycles(bus) == 0x1234_5678_9ABC_DEF0


def test_hi_lo_pair_is_consistent_across_32_bit_rollover(device: Device, bus: Bus) -> None:
    device.clock.advance(2**32 - 1)
    lo = bus.read32(LO)
    device.clock.advance(1)  # low half rolls over between the two bus reads
    hi = bus.read32(HI)
    # Without the latch HI would already be 1, giving 2^33 - 1: off by 2^32 cycles (~17 s).
    assert (lo, hi) == (0xFFFF_FFFF, 0)
    assert (hi << 32) | lo == 2**32 - 1
    # The next pair sees the new value.
    assert read_cycles(bus) == 2**32


def test_reading_hi_first_after_reset_returns_zero(device: Device, bus: Bus) -> None:
    device.clock.advance(5 * 2**32)
    assert bus.read32(HI) == 0  # no LO read yet, so the shadow still holds its reset value


def test_reading_hi_first_returns_the_stale_shadow(device: Device, bus: Bus) -> None:
    device.clock.advance(2**32)
    bus.read32(LO)  # latches HI = 1
    device.clock.advance(2**32)  # counter's high half is now 2
    assert bus.read32(HI) == 1


def test_reading_hi_does_not_relatch(device: Device, bus: Bus) -> None:
    device.clock.advance(2**32)
    bus.read32(LO)
    device.clock.advance(2**32)
    assert bus.read32(HI) == 1
    assert bus.read32(HI) == 1  # only a LO read updates the shadow
