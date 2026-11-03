"""The alert engine against real Postgres and Redis."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from api.alerts import engine
from api.alerts.engine import evaluate_once, run_engine
from api.config import Settings
from api.events import events_channel
from api.incidents import acknowledge, cooldown_key, resolve
from api.main import create_redis
from tests.conftest import API_KEY, ClientFactory

HEADERS = {"X-API-Key": API_KEY}
BAD = 97.0
GOOD = 10.0


async def add_host(db: asyncpg.Pool, host: str = "host-1") -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ($1) ON CONFLICT DO NOTHING", host)


async def add_samples(
    db: asyncpg.Pool, value: float, oldest: int = 40, newest: int = 0, host: str = "host-1"
) -> None:
    """One cpu sample every 2s, from `oldest` to `newest` seconds ago."""
    await add_host(db, host)
    now = datetime.now(UTC)
    rows = [
        (host, "cpu", value, now - timedelta(seconds=ago)) for ago in range(oldest, newest - 1, -2)
    ]
    await db.executemany(
        "INSERT INTO metrics (host_id, name, value, ts) VALUES ($1, $2, $3, $4)", rows
    )


async def incidents(db: asyncpg.Pool) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT i.*, r.name AS rule FROM incidents i JOIN alert_rules r ON r.id = i.rule_id
        ORDER BY i.id
        """
    )


async def rule_id(db: asyncpg.Pool, name: str = "High CPU") -> int:
    return await db.fetchval("SELECT id FROM alert_rules WHERE name = $1", name)


# --- firing ---


async def test_sustained_breach_opens_one_incident(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    rows = await incidents(db)
    assert [(r["host_id"], r["rule"], r["status"]) for r in rows] == [
        ("host-1", "High CPU", "open")
    ]
    assert rows[0]["message"] == "cpu > 90 for 30s"


async def test_only_the_breaching_host_gets_an_incident(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD, host="host-1")
    await add_samples(db, GOOD, host="host-2")
    await redis_client.set("host:host-2", 1, ex=30)
    await redis_client.set("host:host-1", 1, ex=30)
    await evaluate_once(db, redis_client, settings)
    assert [r["host_id"] for r in await incidents(db)] == ["host-1"]


async def test_short_breach_opens_nothing(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, GOOD, oldest=40, newest=22)
    await add_samples(db, BAD, oldest=20)
    await evaluate_once(db, redis_client, settings)
    assert await incidents(db) == []


async def test_disabled_rule_opens_nothing(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await db.execute("UPDATE alert_rules SET enabled = FALSE WHERE name = 'High CPU'")
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    assert await incidents(db) == []


# --- required: idempotency ---


async def test_many_concurrent_evaluators_open_exactly_one_incident(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(events_channel(redis_client))
        await pubsub.get_message(timeout=2)  # the subscribe confirmation

        await asyncio.gather(*(evaluate_once(db, redis_client, settings) for _ in range(50)))

        events = []
        while message := await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2):
            events.append(json.loads(message["data"]))

    rows = await incidents(db)
    assert [r["status"] for r in rows] == ["open"]
    assert [(e["type"], e["id"]) for e in events] == [("incident", rows[0]["id"])]


# --- required: flapping ---


async def test_oscillating_metric_opens_nothing_across_many_ticks(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_host(db)
    now = datetime.now(UTC)
    rows = [
        ("host-1", "cpu", BAD if ago % 4 == 0 else GOOD, now - timedelta(seconds=ago))
        for ago in range(40, -1, -2)
    ]
    await db.executemany(
        "INSERT INTO metrics (host_id, name, value, ts) VALUES ($1, $2, $3, $4)", rows
    )
    for _ in range(5):
        await evaluate_once(db, redis_client, settings)
    assert await incidents(db) == []


async def test_recovery_then_new_breach_is_blocked_only_by_the_cooldown(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["open"]

    # Recovers: auto-resolved, and the cooldown is set with a TTL.
    await add_samples(db, GOOD, oldest=0)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]
    key = cooldown_key(await rule_id(db), "host-1")
    assert 0 < await redis_client.ttl(key) <= 60

    # Breaches again for the full duration, but within the cooldown: no new incident.
    await db.execute("DELETE FROM metrics")
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]

    # The cooldown is the only thing blocking it: once expired, the same data fires.
    await redis_client.delete(key)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved", "open"]


async def test_manual_resolve_is_not_reopened_on_the_next_tick(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    await resolve(db, redis_client, (await incidents(db))[0]["id"])

    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]


# --- auto-resolve ---


async def test_acknowledged_incident_auto_resolves(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    await acknowledge(db, redis_client, (await incidents(db))[0]["id"])

    await add_samples(db, GOOD, oldest=0)
    await evaluate_once(db, redis_client, settings)
    row = (await incidents(db))[0]
    assert row["status"] == "resolved"
    assert row["acknowledged_at"] is not None


async def test_stale_breaching_data_does_not_auto_resolve(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)

    # The host stops reporting: its newest sample is old, and still breaching.
    await db.execute("DELETE FROM metrics")
    await add_samples(db, BAD, oldest=300, newest=280)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["open"]


async def test_disabled_rule_still_auto_resolves(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    await db.execute("UPDATE alert_rules SET enabled = FALSE WHERE name = 'High CPU'")

    await add_samples(db, GOOD, oldest=0)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]


async def test_zero_cooldown_allows_immediate_new_incident(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await db.execute("UPDATE alert_rules SET cooldown_secs = 0")
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    await add_samples(db, GOOD, oldest=0)
    await evaluate_once(db, redis_client, settings)
    assert await redis_client.keys("cooldown:*") == []

    await db.execute("DELETE FROM metrics")
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved", "open"]


# --- required: host down ---


async def test_host_down_after_ttl_expires_and_resolves_on_next_report(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings, make_client: ClientFactory
) -> None:
    short_ttl = settings.model_copy(update={"host_ttl_secs": 2})
    batch = {"samples": [{"host_id": "host-1", "name": "mem", "value": 50}]}
    async with make_client(short_ttl) as client:
        await client.post("/v1/metrics", headers=HEADERS, json=batch)

        await evaluate_once(db, redis_client, short_ttl)
        assert await incidents(db) == [], "the host has just reported"

        await asyncio.sleep(2.5)
        await evaluate_once(db, redis_client, short_ttl)
        rows = await incidents(db)
        assert [(r["host_id"], r["rule"], r["status"]) for r in rows] == [
            ("host-1", "Host down", "open")
        ]
        assert rows[0]["message"] == "no report for 2s"

        await client.post("/v1/metrics", headers=HEADERS, json=batch)
        await evaluate_once(db, redis_client, short_ttl)
        assert [r["status"] for r in await incidents(db)] == ["resolved"]


async def test_missing_key_with_recent_report_is_not_host_down(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    """After a Redis outage or flush, keys are gone for hosts that are still reporting."""
    await add_host(db)  # last_seen is now, and there is no Redis key
    await evaluate_once(db, redis_client, settings)
    assert await incidents(db) == []


async def test_host_down_check_can_be_skipped_at_startup(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_host(db)
    await db.execute("UPDATE hosts SET last_seen = now() - interval '1 hour'")
    await evaluate_once(db, redis_client, settings, check_host_down=False)
    assert await incidents(db) == []
    await evaluate_once(db, redis_client, settings, check_host_down=True)
    assert [r["rule"] for r in await incidents(db)] == ["Host down"]


# --- Redis outage ---


async def test_redis_down_stops_the_tick_and_changes_nothing(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await add_samples(db, BAD, host="host-2")
    await evaluate_once(db, redis_client, settings)
    before = [(r["host_id"], r["status"]) for r in await incidents(db)]

    # host-1 has recovered, host-3 looks stale: neither may change while Redis is down.
    await add_samples(db, GOOD, oldest=0, host="host-1")
    await add_host(db, "host-3")
    await db.execute("UPDATE hosts SET last_seen = now() - interval '1 hour' WHERE id = 'host-3'")

    broken = create_redis("redis://localhost:1/0")
    with pytest.raises(RedisError):
        await evaluate_once(db, broken, settings)
    await broken.aclose()
    assert [(r["host_id"], r["status"]) for r in await incidents(db)] == before


# --- the loop ---


async def test_loop_survives_a_failing_tick(
    db: asyncpg.Pool,
    redis_client: Redis,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = 0

    async def failing_tick(*args: object) -> None:
        nonlocal ticks
        ticks += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "evaluate_once", failing_tick)
    fast = settings.model_copy(update={"alert_interval_secs": 0.01})
    task = asyncio.create_task(run_engine(db, redis_client, fast))
    await asyncio.sleep(0.2)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ticks > 1


async def test_loop_skips_host_down_for_one_ttl_after_starting(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_host(db)
    await db.execute("UPDATE hosts SET last_seen = now() - interval '1 hour'")
    config = settings.model_copy(update={"alert_interval_secs": 0.05, "host_ttl_secs": 1})
    task = asyncio.create_task(run_engine(db, redis_client, config))
    await asyncio.sleep(0.5)
    assert await incidents(db) == [], "still within one TTL of starting"
    await asyncio.sleep(0.8)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [r["rule"] for r in await incidents(db)] == ["Host down"]


async def test_app_lifespan_runs_the_engine_and_stops_it(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings, make_client: ClientFactory
) -> None:
    await add_samples(db, BAD)
    await redis_client.set("host:host-1", 1, ex=30)
    enabled = settings.model_copy(
        update={"alert_engine_enabled": True, "alert_interval_secs": 0.05}
    )
    tasks_before = asyncio.all_tasks()
    async with make_client(enabled):
        async with asyncio.timeout(5):
            # Polling on purpose: the condition lives in Postgres, so no Event can signal it.
            while not await incidents(db):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
    assert [r["rule"] for r in await incidents(db)] == ["High CPU"]
    assert asyncio.all_tasks() - tasks_before == set(), "the engine task must be gone"
