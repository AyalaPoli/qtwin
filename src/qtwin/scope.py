"""Virtual oscilloscope: a passive, read-only recorder of every output change.

Each edge keeps two times: the cycle the command asked for (ideal) and the cycle the output really
changed (actual). Every later measurement (skew, drift, latency) is built on their difference.
The device writes edges in; everyone else (tests, driver calibration, plots) only reads them.
"""

from dataclasses import dataclass

LANES: tuple[str, ...] = (
    *(f"AOM{i}" for i in range(4)),
    *(f"GPIO{i}" for i in range(4)),
    *(f"SNSPD{i}" for i in range(2)),
)
"""The 10 physical channels, one per component block, in display order."""


def component(channel: str) -> str:
    """Lane of a register name: "AOM1.AMPL" -> "AOM1". All fields of one AOM share one output."""
    return channel.split(".", 1)[0]


@dataclass(frozen=True)
class Edge:
    """One applied output write.

    channel: output register name, e.g. "GPIO0.LEVEL".
    ideal_cycle: cycle the command was stamped for (4 ns each).
    actual_cycle: cycle the output really changed. Later than ideal for a late command.
    value: new 32-bit register value.
    """

    channel: str
    ideal_cycle: int
    actual_cycle: int
    value: int

    @property
    def offset(self) -> int:
        """actual - ideal, in cycles. Positive means the output changed late."""
        return self.actual_cycle - self.ideal_cycle


class Scope:
    """Append-only trace of output edges, in the order the sequencer issued them.

    Issue order is ideal timestamp with FIFO order for ties. With per-lane skew, actual_cycle can go
    backwards from one edge to the next across lanes; within one lane it never does.
    """

    def __init__(self) -> None:
        self._edges: list[Edge] = []

    def record(self, channel: str, ideal_cycle: int, actual_cycle: int, value: int) -> None:
        """Device side: log one output write. Cycles are absolute device cycles."""
        self._edges.append(Edge(channel, ideal_cycle, actual_cycle, value))

    @property
    def edges(self) -> tuple[Edge, ...]:
        """Every edge so far. A tuple copy, so readers can't alter the trace."""
        return tuple(self._edges)


# --- Measurement helpers: pure functions over a trace, integer cycles only. ---


def offset_vs_time(edges: tuple[Edge, ...], lane: str) -> list[tuple[int, int]]:
    """(ideal_cycle, offset) for every edge on `lane` (e.g. "AOM1"), in trace order. Cycles.

    Constant offset means static skew; an offset that grows with ideal_cycle means drift.
    """
    return [(edge.ideal_cycle, edge.offset) for edge in edges if component(edge.channel) == lane]


def channel_offsets(edges: tuple[Edge, ...]) -> dict[str, int]:
    """Offset in cycles of the first recorded edge on each lane that has edges.

    The earliest edge, not an average: drift has had the least time to add to it, so it is the
    closest single reading of the static skew (the same idea as M6's calibration edge).
    """
    offsets: dict[str, int] = {}
    for edge in edges:
        offsets.setdefault(component(edge.channel), edge.offset)
    return offsets


def edge_delay(edges: tuple[Edge, ...], src: str, dst: str) -> list[int]:
    """For each edge on lane `src`, cycles until the first `dst` edge at or after it.

    Uses actual cycles: this measures what the outputs physically did (e.g. click -> AOM response).
    A `src` edge with no later `dst` edge is skipped, so the list can be shorter than src's edges.
    """
    dst_times = [edge.actual_cycle for edge in edges if component(edge.channel) == dst]
    delays: list[int] = []
    for edge in edges:
        if component(edge.channel) != src:
            continue
        later = [t for t in dst_times if t >= edge.actual_cycle]
        if later:
            delays.append(min(later) - edge.actual_cycle)
    return delays
