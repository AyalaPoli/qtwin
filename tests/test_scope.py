"""Minimal Scope sink (M2): an append-only record of output changes."""

import pytest

from qtwin.scope import Edge, Scope


def test_records_edges_in_order() -> None:
    scope = Scope()
    scope.record(cycle=10, channel="GPIO0.LEVEL", value=1)
    scope.record(cycle=10, channel="AOM1.AMPL", value=5)
    assert scope.edges == (Edge(10, "GPIO0.LEVEL", 1), Edge(10, "AOM1.AMPL", 5))


def test_edges_is_a_read_only_snapshot() -> None:
    scope = Scope()
    scope.record(cycle=1, channel="GPIO0.LEVEL", value=1)
    snapshot = scope.edges
    scope.record(cycle=2, channel="GPIO0.LEVEL", value=0)
    assert len(snapshot) == 1
    assert isinstance(snapshot, tuple)


def test_edge_is_immutable() -> None:
    edge = Edge(1, "GPIO0.LEVEL", 1)
    with pytest.raises(AttributeError):
        edge.cycle = 2  # type: ignore[misc]
