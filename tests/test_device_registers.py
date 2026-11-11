"""Device register file seen through the Bus: reset values, access rules, address decoding."""

import pytest

from qtwin.bus import Bus
from qtwin.device import registers as r
from qtwin.device.device import Device
from qtwin.device.registers import Access, BusError, Register

READABLE = [reg for reg in r.REGISTERS if reg.access is not Access.WO]
READ_ONLY = [reg for reg in r.REGISTERS if reg.access is Access.RO]
# CTRL is RW too, but writing its RESET bit resets the device; it has its own tests.
READ_WRITE = [reg for reg in r.REGISTERS if reg.access is Access.RW and reg.name != "SYSTEM.CTRL"]


def _ids(regs: list[Register]) -> list[str]:
    return [reg.name for reg in regs]


@pytest.fixture
def device() -> Device:
    return Device()


@pytest.fixture
def bus(device: Device) -> Bus:
    return Bus(device)


@pytest.mark.parametrize("reg", READABLE, ids=_ids(READABLE))
def test_reset_values(bus: Bus, reg: Register) -> None:
    assert bus.read32(reg.address) == reg.reset


@pytest.mark.parametrize("reg", READ_ONLY, ids=_ids(READ_ONLY))
def test_write_to_read_only_raises_and_keeps_value(bus: Bus, reg: Register) -> None:
    with pytest.raises(BusError):
        bus.write32(reg.address, 0x1234)
    assert bus.read32(reg.address) == reg.reset


UNMAPPED = [
    r.BASE_ADDRESS + 0x018,  # gap after SYSTEM.IRQ_ENABLE
    r.BASE_ADDRESS + 0x114,  # gap after AOM0.SKEW_TRIM
    r.BASE_ADDRESS + 0xD00,  # past the FIFO block
    r.BASE_ADDRESS - 4,  # below the device
    0,
]


@pytest.mark.parametrize("address", UNMAPPED, ids=hex)
def test_unmapped_address_raises(bus: Bus, address: int) -> None:
    with pytest.raises(BusError):
        bus.read32(address)
    with pytest.raises(BusError):
        bus.write32(address, 0)


@pytest.mark.parametrize(
    "address", [r.BASE_ADDRESS + 1, r.BASE_ADDRESS + 2, r.addr("AOM0.AMPL") + 3], ids=hex
)
def test_unaligned_address_raises(bus: Bus, address: int) -> None:
    with pytest.raises(BusError):
        bus.read32(address)
    with pytest.raises(BusError):
        bus.write32(address, 0)


@pytest.mark.parametrize("value", [-1, 2**32])
def test_value_outside_32_bits_raises_and_keeps_value(bus: Bus, value: int) -> None:
    address = r.addr("AOM0.AMPL")
    with pytest.raises(ValueError):
        bus.write32(address, value)
    assert bus.read32(address) == 0


@pytest.mark.parametrize("reg", READ_WRITE, ids=_ids(READ_WRITE))
@pytest.mark.parametrize("value", [0, 1, 0xA5A5_5A5A, r.REG_MASK], ids=hex)
def test_read_write_round_trip(bus: Bus, reg: Register, value: int) -> None:
    bus.write32(reg.address, value)
    assert bus.read32(reg.address) == value


def test_writing_one_register_leaves_the_others_alone(bus: Bus) -> None:
    bus.write32(r.addr("AOM1.FREQ_WORD"), 0xFFFF_FFFF)
    others = [reg for reg in READABLE if reg.name != "AOM1.FREQ_WORD"]
    assert [reg.name for reg in others if bus.read32(reg.address) != reg.reset] == []


@pytest.mark.parametrize("name", ["SYSTEM.STATUS", "SYSTEM.IRQ_STATUS"])
def test_w1c_clears_only_bits_written_as_one(device: Device, bus: Bus, name: str) -> None:
    device.set_status(0b111)
    bus.write32(r.addr(name), 0b101)
    assert bus.read32(r.addr(name)) == 0b010


@pytest.mark.parametrize("name", ["SYSTEM.STATUS", "SYSTEM.IRQ_STATUS"])
def test_w1c_writing_zero_changes_nothing(device: Device, bus: Bus, name: str) -> None:
    device.set_status(0b111)
    bus.write32(r.addr(name), 0)
    assert bus.read32(r.addr(name)) == 0b111


def test_w1c_writing_one_to_a_clear_bit_does_not_set_it(bus: Bus) -> None:
    bus.write32(r.addr("SYSTEM.STATUS"), 0b100)
    assert bus.read32(r.addr("SYSTEM.STATUS")) == 0


def test_set_status_sets_status_and_irq_status(device: Device, bus: Bus) -> None:
    device.set_status(r.STATUS_LATE)
    assert bus.read32(r.addr("SYSTEM.STATUS")) == r.STATUS_LATE
    assert bus.read32(r.addr("SYSTEM.IRQ_STATUS")) == r.IRQ_LATE


def test_reading_cmd_push_raises(bus: Bus) -> None:
    with pytest.raises(BusError):
        bus.read32(r.addr("FIFO.CMD_PUSH"))


def test_writing_cmd_push_is_accepted_without_effect_in_m1(bus: Bus) -> None:
    bus.write32(r.addr("FIFO.CMD_PUSH"), 0x1234)
    assert bus.read32(r.addr("FIFO.LEVEL")) == 0  # the FIFO itself arrives in M2
