"""condition_held: when a rule may fire. No database or Redis."""

from datetime import UTC, datetime, timedelta

import pytest

from api.alerts.evaluate import MAX_GAP, Sample, breaches, condition_held

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
DURATION = timedelta(seconds=30)
BAD = 97.0
GOOD = 10.0


def series(*points: tuple[int, float]) -> list[Sample]:
    """Each point is (seconds before NOW, value)."""
    return [Sample(NOW - timedelta(seconds=ago), value) for ago, value in points]


def steady(value: float, oldest: int = 40, every: int = 2) -> list[tuple[int, float]]:
    return [(ago, value) for ago in range(oldest, -1, -every)]


def held(points: list[tuple[int, float]], op: str = ">", threshold: float = 90) -> bool:
    return condition_held(series(*points), op, threshold, DURATION, NOW)


def test_sustained_breach_fires() -> None:
    assert held(steady(BAD))


def test_samples_may_arrive_in_any_order() -> None:
    assert held(list(reversed(steady(BAD))))


def test_less_than_operator() -> None:
    assert held(steady(GOOD), op="<", threshold=20)
    assert not held(steady(BAD), op="<", threshold=20)


def test_healthy_metric_does_not_fire() -> None:
    assert not held(steady(GOOD))


def test_value_exactly_at_threshold_does_not_breach() -> None:
    assert not breaches(90.0, ">", 90.0)
    assert not breaches(90.0, "<", 90.0)
    assert not held(steady(90.0))


def test_breach_shorter_than_duration_does_not_fire() -> None:
    assert not held(steady(GOOD, oldest=40)[:6] + steady(BAD, oldest=28))


def test_breach_just_over_duration_fires() -> None:
    assert held([(40, GOOD), (32, GOOD)] + steady(BAD, oldest=30))


def test_breach_starting_just_inside_the_window_does_not_fire() -> None:
    # The sample proving the state at the window start (31s ago) is healthy, so the
    # condition has held for only 29s.
    assert not held([(31, GOOD)] + steady(BAD, oldest=29))


def test_oscillating_metric_does_not_fire() -> None:
    assert not held([(ago, BAD if ago % 4 == 0 else GOOD) for ago in range(40, -1, -2)])


def test_single_healthy_sample_in_the_window_blocks_firing() -> None:
    points = [(ago, GOOD if ago == 14 else BAD) for ago in range(40, -1, -2)]
    assert not held(points)


def test_single_bad_sample_does_not_fire() -> None:
    assert not held([(0, BAD)])


def test_no_samples_does_not_fire() -> None:
    assert not held([])


def test_new_host_without_enough_history_does_not_fire() -> None:
    assert not held(steady(BAD, oldest=29))


def with_gap(seconds: int) -> list[tuple[int, float]]:
    """All bad, with no samples for `seconds` after the sample 20s ago."""
    before = [(ago, BAD) for ago in range(40, 19, -2)]
    return before + [(ago, BAD) for ago in range(20 - seconds, -1, -1)]


def test_gap_in_the_data_does_not_fire() -> None:
    assert not held(with_gap(int(MAX_GAP.total_seconds()) + 1))


def test_gap_at_exactly_the_limit_still_fires() -> None:
    assert held(with_gap(int(MAX_GAP.total_seconds())))


def test_stale_data_does_not_fire() -> None:
    # All bad, but the host stopped reporting 25s ago.
    assert not held([(ago, BAD) for ago in range(60, 24, -2)])


def test_samples_from_the_future_are_ignored() -> None:
    # Ingest accepts up to 5 minutes of clock skew. A future sample says nothing about now.
    assert held(steady(BAD) + [(-60, GOOD)])


@pytest.mark.parametrize("value, expected", [(90.1, True), (89.9, False)])
def test_threshold_boundary(value: float, expected: bool) -> None:
    assert breaches(value, ">", 90.0) is expected
