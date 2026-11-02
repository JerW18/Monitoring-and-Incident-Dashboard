"""Pure rule logic, with no database or Redis, so every edge case is easy to test."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import NamedTuple

# Largest gap between samples that still counts as continuous data.
MAX_GAP = timedelta(seconds=10)


class Sample(NamedTuple):
    ts: datetime
    value: float


def breaches(value: float, op: str, threshold: float) -> bool:
    return value > threshold if op == ">" else value < threshold


def condition_held(
    samples: Iterable[Sample], op: str, threshold: float, duration: timedelta, now: datetime
) -> bool:
    """True only if the condition demonstrably held for the whole of [now - duration, now]."""
    start = now - duration
    ordered = sorted(s for s in samples if s.ts <= now)

    # The last sample at or before the window start proves the state as the window opened.
    # Without one (a new host, or too little history) the full duration isn't proven.
    before_start = [i for i, s in enumerate(ordered) if s.ts <= start]
    if not before_start:
        return False
    relevant = ordered[before_start[-1] :]

    if not all(breaches(s.value, op, threshold) for s in relevant):
        return False

    # A gap means we don't know what happened in it, and a stale last sample means we
    # don't know the current state. Neither proves the condition held.
    times = [s.ts for s in relevant] + [now]
    return all(later - earlier <= MAX_GAP for earlier, later in zip(times, times[1:], strict=False))
