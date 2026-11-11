"""Virtual oscilloscope: a passive, read-only recorder of every output change.

M2 version: just the edge list. M3 adds ideal vs actual cycle per edge and measurement helpers.
The device writes edges in; everyone else (tests, driver calibration, plots) only reads them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    """One applied output write.

    cycle: device cycle (4 ns each) at which the output changed.
    channel: output register name, e.g. "GPIO0.LEVEL".
    value: new 32-bit register value.
    """

    cycle: int
    channel: str
    value: int


class Scope:
    """Append-only trace of output edges, in the order they happened."""

    def __init__(self) -> None:
        self._edges: list[Edge] = []

    def record(self, cycle: int, channel: str, value: int) -> None:
        """Device side: log one output write. `cycle` in cycles."""
        self._edges.append(Edge(cycle, channel, value))

    @property
    def edges(self) -> tuple[Edge, ...]:
        """Every edge so far. A tuple copy, so readers can't alter the trace."""
        return tuple(self._edges)
