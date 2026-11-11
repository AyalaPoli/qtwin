"""Command dataclass and the 4-word CMD_PUSH frame format (pure functions in registers.py)."""

import pytest

from qtwin.device import registers as r
from qtwin.device.registers import Command

GPIO0 = r.addr("GPIO0.LEVEL")


def test_frame_word_order_is_ts_lo_ts_hi_target_value() -> None:
    cmd = Command(timestamp_cycles=0x1_2345_6789, target=GPIO0, value=7)
    assert r.encode_command(cmd) == (0x2345_6789, 0x1, GPIO0, 7)


def test_round_trip_with_timestamp_above_32_bits() -> None:
    cmd = Command(timestamp_cycles=2**40 + 3, target=r.addr("AOM3.AMPL"), value=0xFFFF_FFFF)
    assert r.decode_command(r.encode_command(cmd)) == cmd


def test_frame_has_cmd_words_words() -> None:
    assert r.CMD_WORDS == 4
    assert len(r.encode_command(Command(0, GPIO0, 1))) == r.CMD_WORDS


def test_output_targets_are_aom_outputs_and_gpio_level_only() -> None:
    names = {r.BY_ADDRESS[a].name for a in r.OUTPUT_TARGETS}
    expected = {f"AOM{i}.{f}" for i in range(4) for f in ("FREQ_WORD", "AMPL", "PHASE", "ENABLE")}
    expected |= {f"GPIO{i}.LEVEL" for i in range(4)}
    assert names == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timestamp_cycles": -1, "target": GPIO0, "value": 0},
        {"timestamp_cycles": 2**64, "target": GPIO0, "value": 0},
        {"timestamp_cycles": 0, "target": GPIO0, "value": -1},
        {"timestamp_cycles": 0, "target": GPIO0, "value": 2**32},
    ],
)
def test_out_of_range_fields_raise(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Command(**kwargs)
