"""Register map (device/registers.py): pure data, checked without any device or bus."""

import ast
import sys
from pathlib import Path

import pytest

from qtwin.device import registers as r
from qtwin.timebase import CLOCK_HZ

REGISTERS_PY = Path(__file__).resolve().parents[1] / "src" / "qtwin" / "device" / "registers.py"

EXPECTED_BLOCKS = {
    "SYSTEM": 0x000,
    "AOM0": 0x100,
    "AOM1": 0x200,
    "AOM2": 0x300,
    "AOM3": 0x400,
    "GPIO0": 0x500,
    "GPIO1": 0x600,
    "GPIO2": 0x700,
    "GPIO3": 0x800,
    "SNSPD0": 0x900,
    "SNSPD1": 0xA00,
    "CLOCK": 0xB00,
    "FIFO": 0xC00,
}


def test_registers_py_imports_only_stdlib() -> None:
    # The driver may import this module (see test_architecture.py). Keeping it stdlib-only
    # guarantees that import can't pull in device state.
    tree = ast.parse(REGISTERS_PY.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in registers.py"
            imported.append(node.module or "")
    non_stdlib = [m for m in imported if m.split(".")[0] not in sys.stdlib_module_names]
    assert non_stdlib == []


def test_block_offsets_match_the_plan() -> None:
    assert r.BLOCKS == EXPECTED_BLOCKS


def test_every_block_has_registers() -> None:
    assert {reg.block for reg in r.REGISTERS} == set(EXPECTED_BLOCKS)


def test_addresses_are_unique() -> None:
    addresses = [reg.address for reg in r.REGISTERS]
    assert len(addresses) == len(set(addresses))


def test_names_are_unique() -> None:
    names = [reg.name for reg in r.REGISTERS]
    assert len(names) == len(set(names))


def test_addresses_are_word_aligned() -> None:
    assert [reg.name for reg in r.REGISTERS if reg.address % 4 != 0] == []


def test_each_register_lies_inside_its_own_block() -> None:
    for reg in r.REGISTERS:
        start = r.BASE_ADDRESS + r.BLOCKS[reg.block]
        assert start <= reg.address < start + r.BLOCK_SIZE, reg.name


@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("SYSTEM.ID", 0x4000_0000),
        ("SYSTEM.IRQ_ENABLE", 0x4000_0014),
        ("AOM1.SKEW_TRIM", 0x4000_0210),
        ("GPIO3.LEVEL", 0x4000_0800),
        ("SNSPD1.LAST_TS", 0x4000_0A08),
        ("CLOCK.CYCLE_HI", 0x4000_0B08),
        ("FIFO.CMD_PUSH", 0x4000_0C00),
        ("FIFO.LOW_WATERMARK", 0x4000_0C0C),
    ],
)
def test_spot_check_addresses(name: str, address: int) -> None:
    assert r.addr(name) == address


def test_unknown_name_raises() -> None:
    with pytest.raises(KeyError):
        r.addr("AOM4.FREQ_WORD")


def test_by_address_is_the_inverse_of_addr() -> None:
    for reg in r.REGISTERS:
        assert r.BY_ADDRESS[r.addr(reg.name)] is reg


def test_reset_values_fit_in_32_bits() -> None:
    assert [reg.name for reg in r.REGISTERS if not 0 <= reg.reset <= r.REG_MASK] == []


def test_freq_hz_reset_matches_timebase() -> None:
    # registers.py can't import timebase (stdlib only), so the two constants are tied here.
    assert r.BY_NAME["CLOCK.FREQ_HZ"].reset == CLOCK_HZ


@pytest.mark.parametrize(
    ("name", "access"),
    [
        ("SYSTEM.ID", r.Access.RO),
        ("SYSTEM.CTRL", r.Access.RW),
        ("SYSTEM.STATUS", r.Access.W1C),
        ("SYSTEM.IRQ_STATUS", r.Access.W1C),
        ("CLOCK.CYCLE_LO", r.Access.RO),
        ("SNSPD0.COUNT", r.Access.RO),
        ("FIFO.CMD_PUSH", r.Access.WO),
        ("FIFO.LOW_WATERMARK", r.Access.RW),
        ("FIFO.DROPPED", r.Access.RO),
    ],
)
def test_access_types(name: str, access: r.Access) -> None:
    assert r.BY_NAME[name].access is access


@pytest.mark.parametrize(
    "masks",
    [
        [r.CTRL_RUN, r.CTRL_RESET],
        [r.STATUS_UNDERRUN, r.STATUS_OVERFLOW, r.STATUS_LATE],
        [r.IRQ_UNDERRUN, r.IRQ_OVERFLOW, r.IRQ_LATE, r.IRQ_FIFO_LOW, r.IRQ_SNSPD0, r.IRQ_SNSPD1],
    ],
)
def test_bit_masks_are_distinct_single_bits(masks: list[int]) -> None:
    assert all(m > 0 and m & (m - 1) == 0 for m in masks)  # each is a power of two
    assert len(set(masks)) == len(masks)


def test_status_bits_share_positions_with_irq_bits() -> None:
    # The device raises STATUS_X and IRQ_X together with one mask, so the positions must agree.
    assert r.STATUS_UNDERRUN == r.IRQ_UNDERRUN
    assert r.STATUS_OVERFLOW == r.IRQ_OVERFLOW
    assert r.STATUS_LATE == r.IRQ_LATE
