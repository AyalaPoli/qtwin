"""Plot smoke tests: each function runs and writes a non-empty PNG. No pixel comparison."""

from pathlib import Path

import pytest

from qtwin.analysis.plots import (
    bus_jitter_plot,
    cli,
    fifo_level_plot,
    main,
    offset_axis_limits,
    offset_bars,
    offset_series,
    offset_vs_time_plot,
    timing_diagram,
)
from qtwin.scenarios import SCENARIOS, ideal_commands, run, run_scenario
from qtwin.scope import Edge, channel_offsets

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


BASE_PLOTS = ["offset_bars.png", "offset_vs_time.png", "timing.png"]
# M5 adds the FIFO-level plot where the FIFO is the story, and the jitter histogram where a
# host streams over the bus. The M3/M4 test expected exactly three files for every scenario.
EXTRA_PLOTS = {
    "bus_jitter": ["bus_jitter.png", "fifo_level.png"],
    "underrun": ["bus_jitter.png", "fifo_level.png"],
    "overflow": ["fifo_level.png"],
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_main_writes_the_plots_of_each_scenario(tmp_path: Path, name: str) -> None:
    paths = main(name, tmp_path)
    assert sorted(p.name for p in paths) == sorted(BASE_PLOTS + EXTRA_PLOTS.get(name, []))
    for path in paths:
        assert path.parent == tmp_path / name
        assert_png(path)


def test_fifo_level_plot_writes_a_png(tmp_path: Path) -> None:
    result = run(SCENARIOS["underrun"])
    assert_png(fifo_level_plot(result.fifo_levels, result.status_events, tmp_path / "f.png"))


def test_fifo_level_plot_handles_an_empty_trace(tmp_path: Path) -> None:
    assert_png(fifo_level_plot((), (), tmp_path / "f.png"))


def test_bus_jitter_plot_writes_a_png(tmp_path: Path) -> None:
    writes = run(SCENARIOS["bus_jitter"]).bus_writes
    assert_png(bus_jitter_plot(writes, lead_cycles=60, path=tmp_path / "j.png"))


def test_cli_defaults_to_ideal(tmp_path: Path) -> None:
    paths = cli(["--out", str(tmp_path)])
    assert {p.parent for p in paths} == {tmp_path / "ideal"}


def test_cli_picks_the_named_scenario(tmp_path: Path) -> None:
    paths = cli(["--scenario", "one_both", "--out", str(tmp_path)])
    assert {p.parent for p in paths} == {tmp_path / "one_both"}


def test_cli_rejects_an_unknown_scenario(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli(["--scenario", "nope", "--out", str(tmp_path)])


def test_offset_axis_spans_at_least_one_cycle_each_way() -> None:
    # All-zero offsets must not autoscale to +-0.05, which shows fractional ticks.
    assert offset_axis_limits([0, 0, 0]) == (-1, 1)
    assert offset_axis_limits([]) == (-1, 1)


def test_offset_axis_covers_the_data_with_one_cycle_headroom() -> None:
    assert offset_axis_limits([0, 3, 1]) == (-1, 4)
    assert offset_axis_limits([-2, 0]) == (-3, 1)


def test_offset_series_has_one_entry_without_calibration() -> None:
    assert list(offset_series("skew_some", calibrate=False)) == ["measured"]


def test_offset_series_adds_after_calibration() -> None:
    series = offset_series("skew_some", calibrate=True)
    assert list(series) == ["measured", "after calibration"]
    assert set(channel_offsets(series["after calibration"]).values()) == {3}


def test_cli_calibrate_writes_plots(tmp_path: Path) -> None:
    paths = cli(["--scenario", "one_both", "--calibrate", "--out", str(tmp_path)])
    assert all(p.exists() for p in paths)
