"""Plot smoke tests: each function runs and writes a non-empty PNG. No pixel comparison."""

from pathlib import Path

import pytest

from qtwin.analysis.plots import (
    main,
    offset_axis_limits,
    offset_bars,
    offset_vs_time_plot,
    timing_diagram,
)
from qtwin.scenarios import ideal_commands, run_scenario
from qtwin.scope import Edge

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def e(channel: str, ideal: int, actual: int, value: int = 1) -> Edge:
    return Edge(channel=channel, ideal_cycle=ideal, actual_cycle=actual, value=value)


SKEWED = (e("GPIO0.LEVEL", 100, 100), e("AOM1.AMPL", 100, 103), e("AOM1.ENABLE", 500, 503, 0))
DRIFTING = tuple(
    e("GPIO0.LEVEL", n, n + n // 20_000, n // 20_000 % 2) for n in range(0, 100_000, 20_000)
)
TRACES = {"ideal": run_scenario(ideal_commands()), "skewed": SKEWED, "drifting": DRIFTING}


def assert_png(path: Path) -> None:
    assert path.stat().st_size > 0
    assert path.read_bytes()[:8] == PNG_MAGIC


@pytest.mark.parametrize("name", TRACES)
def test_timing_diagram_writes_png(tmp_path: Path, name: str) -> None:
    out = timing_diagram(TRACES[name], tmp_path / "timing.png")
    assert_png(out)


@pytest.mark.parametrize("name", TRACES)
def test_offset_bars_writes_png(tmp_path: Path, name: str) -> None:
    out = offset_bars({"measured": TRACES[name]}, tmp_path / "offsets.png")
    assert_png(out)


def test_offset_bars_accepts_a_second_series(tmp_path: Path) -> None:
    # The M6 "after calibration" series slots in as a second entry.
    out = offset_bars({"before": SKEWED, "after": TRACES["ideal"]}, tmp_path / "offsets.png")
    assert_png(out)


@pytest.mark.parametrize("name", TRACES)
def test_offset_vs_time_writes_png(tmp_path: Path, name: str) -> None:
    out = offset_vs_time_plot(TRACES[name], tmp_path / "drift.png")
    assert_png(out)


def test_plot_creates_missing_parent_directory(tmp_path: Path) -> None:
    out = timing_diagram(SKEWED, tmp_path / "new" / "timing.png")
    assert_png(out)


def test_main_writes_the_three_ideal_plots(tmp_path: Path) -> None:
    paths = main(tmp_path)
    assert sorted(p.name for p in paths) == ["offset_bars.png", "offset_vs_time.png", "timing.png"]
    for path in paths:
        assert path.parent == tmp_path
        assert_png(path)


def test_offset_axis_spans_at_least_one_cycle_each_way() -> None:
    # All-zero offsets must not autoscale to +-0.05, which shows fractional ticks.
    assert offset_axis_limits([0, 0, 0]) == (-1, 1)
    assert offset_axis_limits([]) == (-1, 1)


def test_offset_axis_covers_the_data_with_one_cycle_headroom() -> None:
    assert offset_axis_limits([0, 3, 1]) == (-1, 4)
    assert offset_axis_limits([-2, 0]) == (-3, 1)
