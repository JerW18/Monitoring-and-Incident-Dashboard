"""The incident state machine, as pure rules and against real Postgres and Redis."""

import asyncio
import itertools

import asyncpg
import pytest
from redis.asyncio import Redis

from api.incidents import (
    ALLOWED_TRANSITIONS,
    IncidentNotFound,
    InvalidTransition,
    Status,
    acknowledge,
    check_transition,
    cooldown_key,
    resolve,
)
from api.main import create_redis

ALL_PAIRS = list(itertools.product(Status, Status))
INVALID_PAIRS = [pair for pair in ALL_PAIRS if pair not in ALLOWED_TRANSITIONS]


def test_exactly_three_transitions_are_allowed() -> None:
    assert ALLOWED_TRANSITIONS == {
        (Status.OPEN, Status.ACKNOWLEDGED),
        (Status.OPEN, Status.RESOLVED),
        (Status.ACKNOWLEDGED, Status.RESOLVED),
    }


@pytest.mark.parametrize("current, target", sorted(ALLOWED_TRANSITIONS))
def test_valid_transitions_pass(current: Status, target: Status) -> None:
    check_transition(current, target)


@pytest.mark.parametrize("current, target", sorted(INVALID_PAIRS))
def test_invalid_transitions_are_rejected(current: Status, target: Status) -> None:
    with pytest.raises(InvalidTransition):
        check_transition(current, target)


@pytest.fixture
async def incident(db: asyncpg.Pool) -> int:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-1')")
    return await db.fetchval(
        """
        INSERT INTO incidents (host_id, rule_id, message)
        VALUES ('host-1', (SELECT id FROM alert_rules WHERE name = 'High CPU'), 'test')
        RETURNING id
        """
    )


async def test_acknowledge_then_resolve_sets_timestamps(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    acked = await acknowledge(db, redis_client, incident)
    assert acked["status"] == "acknowledged"
    assert acked["acknowledged_at"] is not None
    assert acked["resolved_at"] is None

    resolved = await resolve(db, redis_client, incident)
    assert resolved["status"] == "resolved"
    assert resolved["acknowledged_at"] == acked["acknowledged_at"]
    assert resolved["resolved_at"] is not None


async def test_resolve_directly_from_open(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    resolved = await resolve(db, redis_client, incident)
    assert resolved["status"] == "resolved"
    assert resolved["acknowledged_at"] is None


async def test_invalid_moves_are_rejected_and_change_nothing(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    await acknowledge(db, redis_client, incident)
    with pytest.raises(InvalidTransition):
        await acknowledge(db, redis_client, incident)

    resolved = await resolve(db, redis_client, incident)
    with pytest.raises(InvalidTransition):
        await acknowledge(db, redis_client, incident)
    with pytest.raises(InvalidTransition):
        await resolve(db, redis_client, incident)

    row = await db.fetchrow("SELECT * FROM incidents WHERE id = $1", incident)
    assert row["status"] == "resolved"
    assert row["resolved_at"] == resolved["resolved_at"]


async def test_unknown_incident(db: asyncpg.Pool, redis_client: Redis) -> None:
    with pytest.raises(IncidentNotFound):
        await acknowledge(db, redis_client, 999)
    with pytest.raises(IncidentNotFound):
        await resolve(db, redis_client, 999)


async def test_only_one_of_many_concurrent_resolves_wins(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    results = await asyncio.gather(
        *(resolve(db, redis_client, incident) for _ in range(20)), return_exceptions=True
    )
    assert sum(isinstance(r, InvalidTransition) for r in results) == 19


async def test_acknowledge_racing_resolve_always_ends_resolved(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    await asyncio.gather(
        acknowledge(db, redis_client, incident),
        resolve(db, redis_client, incident),
        return_exceptions=True,
    )
    row = await db.fetchrow("SELECT status, resolved_at FROM incidents WHERE id = $1", incident)
    assert row["status"] == "resolved"
    assert row["resolved_at"] is not None


async def test_resolve_sets_cooldown_with_ttl(
    db: asyncpg.Pool, redis_client: Redis, incident: int
) -> None:
    rule_id = await db.fetchval("SELECT rule_id FROM incidents WHERE id = $1", incident)
    await resolve(db, redis_client, incident)
    assert 0 < await redis_client.ttl(cooldown_key(rule_id, "host-1")) <= 60


async def test_zero_cooldown_sets_no_key(
    db: asyncpg.Pool, redis_client: Redis, incident: int, caplog: pytest.LogCaptureFixture
) -> None:
    await db.execute("UPDATE alert_rules SET cooldown_secs = 0")
    resolved = await resolve(db, redis_client, incident)
    assert resolved["status"] == "resolved"
    assert await redis_client.keys("cooldown:*") == []
    # Redis rejects EX 0. That error would be caught and logged, so check the log too.
    assert "failed to set cooldown" not in caplog.text


async def test_a_person_can_resolve_while_redis_is_down(db: asyncpg.Pool, incident: int) -> None:
    broken = create_redis("redis://localhost:1/0")
    resolved = await resolve(db, broken, incident)
    await broken.aclose()
    assert resolved["status"] == "resolved"
