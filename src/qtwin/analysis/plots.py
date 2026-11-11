"""Plots of a Scope trace, written as PNG files (matplotlib, Agg backend).

Each function takes edges and an output path and returns the path. They use Figure objects with
an explicit Agg canvas instead of pyplot, so there is no global figure state and no GUI backend.
Run `python -m qtwin.analysis.plots --scenario NAME` (default ideal) to write one scenario's plots
to out/NAME/; M5 bus scenarios also get fifo_level.png and bus_jitter.png. Add `--calibrate` to
put an "after calibration" series (M6 trims) in the offset bars.
"""

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from qtwin.analysis.stats import linear_fit
from qtwin.bus import BusWrite
from qtwin.device.registers import CMD_WORDS, FIFO_CAPACITY, STATUS_OVERFLOW, STATUS_UNDERRUN
from qtwin.scenarios import SCENARIOS, calibrate_scenario, run
from qtwin.scope import LANES, Edge, channel_offsets, component, offset_vs_time

# Colors from a validated colorblind-safe categorical palette (slots 1 and 2), plus neutrals.
ACTUAL_COLOR = "#2a78d6"  # blue: what the output really did
SECOND_COLOR = "#eb6834"  # orange: a second series, e.g. "after calibration" in M6
IDEAL_COLOR = "#b4b2a9"  # light gray: what was commanded (reference, so recessive)
TEXT_MUTED = "#6b6a64"
GRID_COLOR = "#e6e4dd"
SERIES_COLORS = (ACTUAL_COLOR, SECOND_COLOR)
# Reserved status colours (never series colours), always paired with a text label.
UNDERRUN_COLOR = "#d03b3b"  # critical: the outputs missed their time
OVERFLOW_COLOR = "#ec835a"  # serious: commands were lost


def _new_figure(width: float, height: float) -> Figure:
    fig = Figure(figsize=(width, height), layout="constrained")
    FigureCanvasAgg(fig)  # attaches the Agg canvas to the figure
    return fig


def _style(ax: Axes) -> None:
    # Recessive frame and grid so the data marks carry the eye.
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT_MUTED, labelsize=8)
    ax.grid(True, color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)


def offset_axis_limits(offsets: Sequence[int]) -> tuple[int, int]:
    """Y limits for an offset axis, in cycles: the data plus 1 cycle of headroom, always
    including 0. Without this, all-zero data autoscales to +-0.05 and shows fractional ticks
    on an axis that can only hold whole cycles.
    """
    return min([0, *offsets]) - 1, max([0, *offsets]) + 1


def _save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    return path


def timing_diagram(edges: tuple[Edge, ...], path: Path) -> Path:
    """Logic-analyzer view: one row per lane (all 10), x axis in cycles (4 ns each).

    Gray tick = ideal (commanded) cycle, blue dot = actual cycle. On time, the dot sits on its
    tick; a late or skewed edge shows as a dot to the right of its tick.
    """
    fig = _new_figure(10, 4.5)
    ax = fig.add_subplot()
    _style(ax)
    rows = {lane: row for row, lane in enumerate(LANES)}
    ys = [rows[component(edge.channel)] for edge in edges]
    ax.plot(
        [edge.ideal_cycle for edge in edges], ys, linestyle="none",
        marker="|", markersize=16, markeredgewidth=2, color=IDEAL_COLOR, label="ideal (commanded)",
    )  # fmt: skip
    ax.plot(
        [edge.actual_cycle for edge in edges], ys, linestyle="none",
        marker="o", markersize=6, color=ACTUAL_COLOR, label="actual (applied)",
    )  # fmt: skip
    ax.set_yticks(range(len(LANES)), LANES)
    ax.set_ylim(len(LANES) - 0.5, -0.5)  # AOM0 at the top, like a logic analyzer
    ax.set_xlabel("cycle (4 ns each)", color=TEXT_MUTED)
    ax.set_title("Output edges: ideal vs actual", loc="left")
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    return _save(fig, path)


def offset_bars(series: Mapping[str, tuple[Edge, ...]], path: Path) -> Path:
    """Per-lane offset (actual - ideal of the earliest edge, cycles), one bar group per lane.

    series: label -> trace, e.g. {"measured": edges}; M6 adds {"after calibration": ...}.
    At most two series (one color each). A lane with no edges in any series is marked.
    """
    if not 1 <= len(series) <= len(SERIES_COLORS):
        raise ValueError(f"need 1..{len(SERIES_COLORS)} series, got {len(series)}")
    fig = _new_figure(9, 4)
    ax = fig.add_subplot()
    _style(ax)
    width = 0.8 / len(series)
    used: set[str] = set()
    all_heights: list[int] = []
    for k, (label, edges) in enumerate(series.items()):
        offsets = channel_offsets(edges)
        used |= offsets.keys()
        xs = [i + (k - (len(series) - 1) / 2) * width for i, lane in enumerate(LANES)]
        heights = [offsets.get(lane, 0) for lane in LANES]
        all_heights += heights
        bars = ax.bar(xs, heights, width * 0.9, color=SERIES_COLORS[k], label=label)
        # Label every lane that has edges: a 0-height bar is invisible, so without its "0" a
        # measured zero offset would look the same as a lane with no edges.
        ax.bar_label(
            bars,
            [str(h) if lane in offsets else "" for h, lane in zip(heights, LANES, strict=True)],
            fontsize=8,
            color=TEXT_MUTED,
        )
    for i, lane in enumerate(LANES):
        if lane not in used:
            ax.text(i, 0, "no edges", ha="center", va="bottom", fontsize=7, color=TEXT_MUTED)
    ax.axhline(0, color=TEXT_MUTED, linewidth=0.8)
    ax.set_ylim(*offset_axis_limits(all_heights))
    ax.set_xticks(range(len(LANES)), LANES)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("offset (cycles, 4 ns each)", color=TEXT_MUTED)
    ax.set_title("Per-channel offset (earliest edge)", loc="left")
    if len(series) > 1:
        ax.legend(fontsize=8, frameon=False)
    return _save(fig, path)


def offset_vs_time_plot(edges: tuple[Edge, ...], path: Path) -> Path:
    """Offset vs ideal cycle, one small panel per lane that has edges, with a least-squares line.

    Small multiples instead of one crowded chart: lanes share both axes, so slopes compare
    directly. The panel title gives the fitted slope in ppb (slope * 1e9); a flat line is static
    skew, a sloped one is drift.
    """
    lanes = [lane for lane in LANES if offset_vs_time(edges, lane)]
    cols = 2
    rows = max(1, -(-len(lanes) // cols))  # ceiling division
    fig = _new_figure(9, 1.6 * rows + 0.8)
    axes = fig.subplots(rows, cols, sharex=True, sharey=True, squeeze=False).flatten()
    for ax in axes:
        _style(ax)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    for ax, lane in zip(axes, lanes, strict=False):
        points = offset_vs_time(edges, lane)
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        ax.plot(xs, ys, linestyle="none", marker="o", markersize=5, color=ACTUAL_COLOR)
        if len(set(xs)) >= 2:
            slope, intercept = linear_fit(xs, ys)
            x0, x1 = min(xs), max(xs)
            ax.plot([x0, x1], [slope * x0 + intercept, slope * x1 + intercept],
                    color=ACTUAL_COLOR, linewidth=2, alpha=0.6)  # fmt: skip
            title = f"{lane}: fit {slope * 1e9:.0f} ppb"
        else:
            title = f"{lane}: too few points to fit"
        ax.set_title(title, loc="left", fontsize=9)
    # Axes are shared, so setting the limits once applies to every panel.
    axes[0].set_ylim(*offset_axis_limits([edge.offset for edge in edges]))
    for ax in axes[len(lanes) :]:
        ax.set_visible(False)
    if not lanes:
        axes[0].set_visible(True)
        axes[0].text(0.5, 0.5, "no edges", ha="center", transform=axes[0].transAxes)
    fig.supxlabel("ideal cycle (4 ns each)", color=TEXT_MUTED, fontsize=9)
    fig.supylabel("offset (cycles)", color=TEXT_MUTED, fontsize=9)
    fig.suptitle("Offset vs time per channel (flat = skew, slope = drift)", x=0.02, ha="left")
    return _save(fig, path)


def fifo_level_plot(
    fifo_levels: Sequence[tuple[int, int]],
    status_events: Sequence[tuple[int, int]],
    path: Path,
) -> Path:
    """FIFO level (commands) vs device cycle as a step line, with a dashed line at CAPACITY and
    a vertical marker at every UNDERRUN / OVERFLOW event.

    fifo_levels, status_events: Device.fifo_levels and Device.status_events, (cycle, value).
    A step, not a sloped line: the level jumps at a push or pop and holds in between.
    """
    fig = _new_figure(9, 3.2)
    ax = fig.subplots()
    _style(ax)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if fifo_levels:
        # Start from (first cycle, 0) so the first push shows as a rise from empty.
        xs = [fifo_levels[0][0], *(c for c, _ in fifo_levels)]
        ys = [0, *(level for _, level in fifo_levels)]
        ax.step(xs, ys, where="post", color=ACTUAL_COLOR, linewidth=2)
    else:
        ax.text(0.5, 0.5, "no FIFO activity", ha="center", transform=ax.transAxes)
    top = max((level for _, level in fifo_levels), default=0)
    # Scale to the data so a level of 3 is readable; draw the capacity line only when the data
    # comes near it (otherwise it would squash the trace into the bottom few pixels).
    if top > FIFO_CAPACITY // 2:
        ax.axhline(FIFO_CAPACITY, color=IDEAL_COLOR, linewidth=1, linestyle="--")
        ax.text(1, FIFO_CAPACITY, f"capacity {FIFO_CAPACITY} ", color=TEXT_MUTED, fontsize=8,
                ha="right", va="bottom", transform=ax.get_yaxis_transform())  # fmt: skip
        ax.set_ylim(0, FIFO_CAPACITY * 1.15)
    else:
        ax.set_ylim(0, top + 2)
    for mask, name, color in (
        (STATUS_UNDERRUN, "UNDERRUN", UNDERRUN_COLOR),
        (STATUS_OVERFLOW, "OVERFLOW", OVERFLOW_COLOR),
    ):
        cycles = [c for c, m in status_events if m & mask]
        for cycle in sorted(set(cycles)):
            ax.axvline(cycle, color=color, linewidth=1.5)
        if cycles:
            # Label once, by the first event: the line colour alone must not carry the meaning.
            # The count is events, not distinct cycles (6 drops can share cycle 0).
            ax.text(cycles[0], 0.98, f" {name} x{len(cycles)}", color=TEXT_MUTED, fontsize=8,
                    va="top", transform=ax.get_xaxis_transform())  # fmt: skip
    ax.set_xlabel("device cycle (4 ns each)", color=TEXT_MUTED, fontsize=9)
    ax.set_ylabel("FIFO level (commands)", color=TEXT_MUTED, fontsize=9)
    ax.set_title("FIFO level vs time", loc="left")
    return _save(fig, path)


def bus_jitter_plot(bus_writes: Sequence[BusWrite], lead_cycles: int, path: Path) -> Path:
    """Histogram of bus delay per write (arrival - issue, in cycles), one bar per whole cycle.

    The dashed line is lead_cycles - CMD_WORDS: the largest latency + jitter with margin >= 0.
    Delays left of it cost nothing at the outputs; the FIFO lead absorbs them.
    """
    delays = [w.arrival_cycle - w.issue_cycle for w in bus_writes]
    fig = _new_figure(7, 3.2)
    ax = fig.subplots()
    _style(ax)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    if delays:
        # Bins centred on whole cycles; rwidth leaves a gap between neighbouring bars.
        bins = [d - 0.5 for d in range(min(delays), max(delays) + 2)]
        ax.hist(delays, bins=bins, rwidth=0.85, color=ACTUAL_COLOR)
    else:
        ax.text(0.5, 0.5, "no bus writes", ha="center", transform=ax.transAxes)
    budget = lead_cycles - CMD_WORDS
    ax.axvline(budget, color=TEXT_MUTED, linewidth=1, linestyle="--")
    ax.text(budget, 0.95, f" margin 0 ({budget} cycles)", color=TEXT_MUTED, fontsize=8,
            va="top", transform=ax.get_xaxis_transform())  # fmt: skip
    ax.set_xlabel("bus delay: arrival - issue (cycles)", color=TEXT_MUTED, fontsize=9)
    ax.set_ylabel("writes", color=TEXT_MUTED, fontsize=9)
    ax.set_title(f"Bus delay per write ({len(delays)} writes)", loc="left")
    return _save(fig, path)


def offset_series(scenario: str, calibrate: bool) -> dict[str, tuple[Edge, ...]]:
    """Traces for the offset bar chart: {"measured": ...} and, if `calibrate`, also
    {"after calibration": ...}, the same commands rerun with the trims M6 calibration found.

    After calibration every lane sits at the slowest lane's skew, not at 0: trims only delay.
    """
    s = SCENARIOS[scenario]
    series = {"measured": run(s).edges}
    if calibrate:
        result = calibrate_scenario(scenario)
        trims = {lane: cal.trim_cycles for lane, cal in result.lanes.items()}
        series["after calibration"] = run(s, trims=trims).edges
    return series


def main(
    scenario: str = "ideal", out_dir: Path = Path("out"), calibrate: bool = False
) -> list[Path]:
    """Run `scenario` and write its plots to out_dir/<scenario>/ (created if missing).

    calibrate: also show offsets after M6 calibration in offset_bars.png.
    Raises KeyError for an unknown scenario name.
    """
    series = offset_series(scenario, calibrate)
    edges = series["measured"]
    folder = out_dir / scenario
    paths = [
        timing_diagram(edges, folder / "timing.png"),
        offset_bars(series, folder / "offset_bars.png"),
        offset_vs_time_plot(edges, folder / "offset_vs_time.png"),
    ]
    # M5 plots, only where they have something to show: a streaming host, or dropped commands.
    s = SCENARIOS[scenario]
    result = run(s)
    if s.stream is not None or result.dropped > 0:
        paths.append(
            fifo_level_plot(result.fifo_levels, result.status_events, folder / "fifo_level.png")
        )
    if s.stream is not None:
        paths.append(
            bus_jitter_plot(result.bus_writes, s.stream.lead_cycles, folder / "bus_jitter.png")
        )
    return paths


def cli(argv: Sequence[str] | None = None) -> list[Path]:
    """Command-line entry: parse `argv` (sys.argv if None) and call main.

    An unknown scenario name exits with a usage message (SystemExit), listing the valid ones.
    """
    parser = argparse.ArgumentParser(description="Write a scenario's plots as PNG files.")
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="ideal")
    parser.add_argument("--out", type=Path, default=Path("out"), help="parent output folder")
    parser.add_argument(
        "--calibrate", action="store_true", help='add an "after calibration" offset series'
    )
    args = parser.parse_args(argv)
    return main(args.scenario, args.out, args.calibrate)


if __name__ == "__main__":
    for written in cli():
        print(written)
