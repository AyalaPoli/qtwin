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
    """Append-only trace of output edges, in the order they happened."""

    def __init__(self) -> None:
        self._edges: list[Edge] = []

    def record(self, channel: str, ideal_cycle: int, actual_cycle: int, value: int) -> None:
        """Device side: log one output write. Cycles are absolute device cycles."""
        self._edges.append(Edge(channel, ideal_cycle, actual_cycle, value))

    @property
    def edges(self) -> tuple[Edge, ...]:
        """Every edge so far. A tuple copy, so readers can't alter the trace."""
        return tuple(self._edges)
