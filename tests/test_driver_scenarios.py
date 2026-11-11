"""M7 scenarios: the same experiment run by the polling driver and by the interrupt driver."""

import pytest

from qtwin.device import registers as r
from qtwin.scenarios import DRIVER_SCENARIOS, run_driver
from qtwin.scope import edge_delay

REFILL = DRIVER_SCENARIOS["refill"]
SPARSE = DRIVER_SCENARIOS["sparse_clicks"]


def test_registry() -> None:
    assert list(DRIVER_SCENARIOS) == ["refill", "sparse_clicks"]


def test_slow_polling_underruns_but_interrupts_do_not() -> None:
    polling = run_driver("refill", "polling")
    interrupt = run_driver("refill", "interrupt")
    assert polling.status & r.STATUS_UNDERRUN
    assert interrupt.status == 0
    assert all(e.offset == 0 for e in interrupt.edges)
    assert len(interrupt.edges) == len(REFILL.commands)


def test_polling_reads_far_more_than_interrupts_for_sparse_events() -> None:
    polling = run_driver("sparse_clicks", "polling")
    interrupt = run_driver("sparse_clicks", "interrupt")
    assert polling.bus_reads > 10 * interrupt.bus_reads


def test_polling_latency_is_within_one_period_plus_read_cost() -> None:
    result = run_driver("sparse_clicks", "polling")
    bound = SPARSE.poll_period_cycles + SPARSE.bus.bus_latency_cycles
    assert all(0 <= lat <= bound for lat in result.latencies)


def test_interrupt_latency_is_constant() -> None:
    result = run_driver("sparse_clicks", "interrupt")
    assert set(result.latencies) == {SPARSE.irq_latency_cycles + SPARSE.bus.bus_latency_cycles}


def test_interrupt_feedback_lands_exactly() -> None:
    assert SPARSE.feedback is not None
    result = run_driver("sparse_clicks", "interrupt")
    delays = edge_delay(result.edges, "SNSPD0", "AOM0")
    assert delays == [SPARSE.feedback.feedback_latency_cycles] * len(result.latencies)
    assert result.status == 0


def test_irq_events_mark_every_click() -> None:
    result = run_driver("sparse_clicks", "interrupt")
    clicks = [e.ideal_cycle for e in result.edges if e.channel == "SNSPD0.COUNT"]
    assert [c for c, m in result.irq_events if m & r.IRQ_SNSPD0] == clicks


@pytest.mark.parametrize("kind", ["polling", "interrupt"])
@pytest.mark.parametrize("name", ["refill", "sparse_clicks"])
def test_same_seed_same_result(name: str, kind: str) -> None:
    assert run_driver(name, kind) == run_driver(name, kind)


def test_different_click_seed_gives_different_results() -> None:
    a = run_driver("sparse_clicks", "polling")
    b = run_driver("sparse_clicks", "polling", click_seed=99)
    assert a.latencies != b.latencies


def test_unknown_driver_kind_raises() -> None:
    with pytest.raises(ValueError):
        run_driver("refill", "dma")
