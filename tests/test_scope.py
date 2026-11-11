"""Scope: an append-only record of output changes, ideal (commanded) vs actual (applied) cycle."""

import pytest

from qtwin.scope import LANES, Edge, Scope, component


def test_records_edges_in_order() -> None:
    scope = Scope()
    scope.record(channel="GPIO0.LEVEL", ideal_cycle=10, actual_cycle=10, value=1)
    scope.record(channel="AOM1.AMPL", ideal_cycle=10, actual_cycle=12, value=5)
    assert scope.edges == (
        Edge(channel="GPIO0.LEVEL", ideal_cycle=10, actual_cycle=10, value=1),
        Edge(channel="AOM1.AMPL", ideal_cycle=10, actual_cycle=12, value=5),
    )


def test_offset_is_actual_minus_ideal() -> None:
    edge = Edge(channel="GPIO0.LEVEL", ideal_cycle=10, actual_cycle=13, value=1)
    assert edge.offset == 3


def test_edges_is_a_read_only_snapshot() -> None:
    scope = Scope()
    scope.record(channel="GPIO0.LEVEL", ideal_cycle=1, actual_cycle=1, value=1)
    snapshot = scope.edges
    scope.record(channel="GPIO0.LEVEL", ideal_cycle=2, actual_cycle=2, value=0)
    assert len(snapshot) == 1
    assert isinstance(snapshot, tuple)


def test_edge_is_immutable() -> None:
    edge = Edge(channel="GPIO0.LEVEL", ideal_cycle=1, actual_cycle=1, value=1)
    with pytest.raises(AttributeError):
        edge.actual_cycle = 2  # type: ignore[misc]


def test_component_is_the_block_name() -> None:
    assert component("AOM1.AMPL") == "AOM1"
    assert component("GPIO3.LEVEL") == "GPIO3"


def test_ten_lanes_one_per_physical_channel() -> None:
    assert LANES == (
        "AOM0", "AOM1", "AOM2", "AOM3",
        "GPIO0", "GPIO1", "GPIO2", "GPIO3",
        "SNSPD0", "SNSPD1",
    )  # fmt: skip
