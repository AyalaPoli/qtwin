"""SNSPD click model: when the detectors fire, as plain integer cycles.

A superconducting nanowire single-photon detector (SNSPD) gives a short electrical pulse, a
"click", when it absorbs a photon. The model does not simulate photons or physics: a click is
just a cycle number. Real photon arrivals are random (roughly Poisson); here the gaps between
clicks are uniform integers from a seeded generator [assumed], which keeps all scheduling in
integer cycles and makes every run repeatable from its seed.

The click list is a hidden input of the Device, like the timing faults: the driver can't see it,
only COUNT, LAST_TS and the IRQ bit the clicks produce.
"""

import random

DETECTORS: tuple[str, ...] = ("SNSPD0", "SNSPD1")
"""Detector lanes, in IRQ-bit order (IRQ_SNSPD0, IRQ_SNSPD1)."""


def seeded_clicks(seed: int, start: int, count: int, min_gap: int, max_gap: int) -> tuple[int, ...]:
    """`count` click cycles after `start`, each gap drawn uniformly from min_gap..max_gap cycles.

    seed: seeds a private generator; same arguments, same clicks.
    start: absolute cycle the first gap is counted from (>= 0). The first click is at
        start + gap, never on `start` itself.
    min_gap, max_gap: bounds of each gap in cycles, 1 <= min_gap <= max_gap. min_gap >= 1 means
        one click per cycle at most: a real detector is blind for a while after each click.
    """
    if start < 0 or count < 0 or not 1 <= min_gap <= max_gap:
        raise ValueError(
            f"need start >= 0, count >= 0, 1 <= min_gap <= max_gap; "
            f"got {start}, {count}, {min_gap}, {max_gap}"
        )
    # A private generator, as in bus.py: the clicks depend only on the arguments.
    rng = random.Random(seed)
    clicks: list[int] = []
    t = start
    for _ in range(count):
        t += rng.randint(min_gap, max_gap)
        clicks.append(t)
    return tuple(clicks)
