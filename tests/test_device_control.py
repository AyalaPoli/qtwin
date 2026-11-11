"""SYSTEM block behaviour: CTRL run/reset, sticky STATUS bits, and the IRQ line."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import Access

CTRL = r.addr("SYSTEM.CTRL")
STATUS = r.addr("SYSTEM.STATUS")
IRQ_STATUS = r.addr("SYSTEM.IRQ_STATUS")
IRQ_ENABLE = r.addr("SYSTEM.IRQ_ENABLE")


@pytest.fixture
def device() -> Device:
    return Device()


@pytest.fixture
def bus(device: Device) -> Bus:
    return Bus(device)


# --- CTRL ---


def test_run_bit_reads_back(bus: Bus) -> None:
    bus.write32(CTRL, r.CTRL_RUN)
    assert bus.read32(CTRL) == r.CTRL_RUN


def test_reset_restores_every_register_and_the_clock(device: Device, bus: Bus) -> None:
    # Dirty as much state as possible first.
    for reg in r.REGISTERS:
        if reg.access is Access.RW and reg.name != "SYSTEM.CTRL":
            bus.write32(reg.address, 0xFFFF_FFFF)
    bus.write32(CTRL, r.CTRL_RUN)
    device.set_status(r.STATUS_LATE | r.IRQ_FIFO_LOW)
    device.clock.advance(3 * 2**32 + 7)
    bus.read32(r.addr("CLOCK.CYCLE_LO"))  # latches CYCLE_HI = 3

    bus.write32(CTRL, r.CTRL_RESET)

    assert device.clock.now == 0
    readable = [reg for reg in r.REGISTERS if reg.access is not Access.WO]
    assert [reg.name for reg in readable if bus.read32(reg.address) != reg.reset] == []


def test_reset_bit_self_clears(bus: Bus) -> None:
    bus.write32(CTRL, r.CTRL_RUN | r.CTRL_RESET)
    # Reset wins over RUN written in the same word: the device comes back stopped.
    assert bus.read32(CTRL) == 0


def test_reset_clears_the_cycle_hi_shadow(device: Device, bus: Bus) -> None:
    device.clock.advance(2**32)
    bus.read32(r.addr("CLOCK.CYCLE_LO"))
    bus.write32(CTRL, r.CTRL_RESET)
    assert bus.read32(r.addr("CLOCK.CYCLE_HI")) == 0


def test_reset_clears_the_irq_line(device: Device, bus: Bus) -> None:
    bus.write32(IRQ_ENABLE, r.IRQ_LATE)
    device.set_status(r.STATUS_LATE)
    bus.write32(CTRL, r.CTRL_RESET)
    assert device.irq_line is False


# --- Sticky STATUS ---


def test_status_bit_stays_set_until_cleared(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_LATE)
    # Time passes and the host does unrelated work: the bit must not fade on its own.
    for _ in range(3):
        device.clock.advance(1_000_000)
        bus.write32(r.addr("AOM0.AMPL"), 5)
        assert bus.read32(STATUS) == r.STATUS_LATE
    bus.write32(STATUS, r.STATUS_LATE)
    assert bus.read32(STATUS) == 0


def test_reading_status_does_not_clear_it(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_OVERFLOW)
    bus.read32(STATUS)
    assert bus.read32(STATUS) == r.STATUS_OVERFLOW


def test_new_events_accumulate(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_UNDERRUN)
    device.set_status(r.STATUS_LATE)
    assert bus.read32(STATUS) == r.STATUS_UNDERRUN | r.STATUS_LATE


def test_clearing_status_leaves_irq_status_alone(device: Device, bus: Bus) -> None:
    # Two separate registers: acknowledging the interrupt and clearing the error flag are
    # separate host actions.
    device.set_status(r.STATUS_LATE)
    bus.write32(STATUS, r.STATUS_LATE)
    assert bus.read32(IRQ_STATUS) == r.IRQ_LATE


# --- IRQ line = (IRQ_STATUS & IRQ_ENABLE) != 0 ---


def test_irq_line_low_after_reset(device: Device) -> None:
    assert device.irq_line is False


def test_irq_line_needs_both_pending_and_enabled(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_LATE)
    assert device.irq_line is False  # pending, but masked
    bus.write32(IRQ_ENABLE, r.IRQ_LATE)
    assert device.irq_line is True
    bus.write32(IRQ_STATUS, r.IRQ_LATE)  # ISR acknowledges
    assert device.irq_line is False


def test_irq_line_ignores_enabled_bits_that_are_not_pending(device: Device, bus: Bus) -> None:
    bus.write32(IRQ_ENABLE, r.IRQ_UNDERRUN | r.IRQ_OVERFLOW)
    device.set_status(r.STATUS_LATE)
    assert device.irq_line is False


def test_masking_lowers_the_line_but_keeps_the_event_pending(device: Device, bus: Bus) -> None:
    bus.write32(IRQ_ENABLE, r.IRQ_LATE)
    device.set_status(r.STATUS_LATE)
    bus.write32(IRQ_ENABLE, 0)
    assert device.irq_line is False
    assert bus.read32(IRQ_STATUS) == r.IRQ_LATE
