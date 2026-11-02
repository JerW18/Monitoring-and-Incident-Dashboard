"""POST /v1/metrics and GET /v1/metrics against real Postgres and Redis."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis

from api.config import Settings
from api.routes.metrics import MAX_BODY_BYTES, MAX_HISTORY_ROWS, MAX_SAMPLES
from tests.conftest import API_KEY, ClientFactory

HEADERS = {"X-API-Key": API_KEY}


def sample(host: str = "host-1", name: str = "cpu", value: float = 42.5, **extra: Any) -> dict:
    return {"host_id": host, "name": name, "value": value, **extra}


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) + delta).isoformat()


@pytest.fixture
async def client(
    settings: Settings, make_client: ClientFactory, redis_client: Redis
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(settings) as c:
        yield c


async def post(client: httpx.AsyncClient, *samples: dict) -> httpx.Response:
    return await client.post("/v1/metrics", headers=HEADERS, json={"samples": list(samples)})


# --- auth ---


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"X-API-Key": "wrong"}, id="wrong"),
        pytest.param({"X-API-Key": ""}, id="empty"),
        pytest.param({"X-API-Key": "caf\xe9".encode("latin-1")}, id="non-ascii"),
    ],
)
async def test_bad_key_is_rejected(client: httpx.AsyncClient, headers: dict) -> None:
    resp = await client.post("/v1/metrics", headers=headers, json={"samples": [sample()]})
    assert resp.status_code == 401


async def test_auth_is_checked_before_the_body_is_parsed(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/metrics", content=b"{not json")
    assert resp.status_code == 401


def test_empty_api_key_setting_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="x", redis_url="x", api_key=SecretStr(""))


# --- validation ---


async def test_oversized_body_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/metrics", headers=HEADERS, content=b" " * (MAX_BODY_BYTES + 1))
    assert resp.status_code == 413


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"{not json", id="malformed-json"),
        pytest.param(b"\xff\xfe", id="invalid-utf8"),
        pytest.param(b"", id="empty-body"),
        pytest.param(b'{"samples": []}', id="empty-batch"),
        pytest.param(b'{"samples": [], "x": 1}', id="unknown-top-level-field"),
        pytest.param(
            b'{"samples": [{"host_id": "h", "name": "cpu", "value": 1e999}]}', id="infinite-value"
        ),
        pytest.param(
            b'{"samples": [{"host_id": "h", "name": "cpu", "value": NaN}]}', id="nan-value"
        ),
    ],
)
async def test_invalid_raw_body_is_rejected(client: httpx.AsyncClient, payload: bytes) -> None:
    resp = await client.post("/v1/metrics", headers=HEADERS, content=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(sample(host="Host_1"), id="host-bad-chars"),
        pytest.param(sample(host=""), id="host-empty"),
        pytest.param(sample(host="h" * 65), id="host-too-long"),
        pytest.param(sample(host="host-1\n"), id="host-trailing-newline"),
        pytest.param(sample(name="CPU"), id="name-uppercase"),
        pytest.param(sample(name="n" * 33), id="name-too-long"),
        pytest.param(sample(value="97"), id="value-string"),  # type: ignore[arg-type]
        pytest.param(sample(value=True), id="value-bool"),
        pytest.param(sample(ts="2026-01-01T00:00:00"), id="ts-without-timezone"),
        pytest.param(sample(ts=iso(timedelta(minutes=-6))), id="ts-too-old"),
        pytest.param(sample(ts=iso(timedelta(minutes=6))), id="ts-too-new"),
        pytest.param(sample(ts="0001-01-01T00:00:00+14:00"), id="ts-year-one"),
        pytest.param(sample(extra="x"), id="unknown-field"),
    ],
)
async def test_invalid_sample_is_rejected_and_nothing_is_stored(
    client: httpx.AsyncClient, db: asyncpg.Pool, bad: dict
) -> None:
    resp = await post(client, sample(host="good"), bad)
    assert resp.status_code == 422
    assert await db.fetchval("SELECT count(*) FROM metrics") == 0
    assert await db.fetchval("SELECT count(*) FROM hosts") == 0


async def test_batch_size_limit(client: httpx.AsyncClient, db: asyncpg.Pool) -> None:
    assert (await post(client, *[sample()] * (MAX_SAMPLES + 1))).status_code == 422
    resp = await post(client, *[sample()] * MAX_SAMPLES)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": MAX_SAMPLES}
    assert await db.fetchval("SELECT count(*) FROM metrics") == MAX_SAMPLES


# --- successful ingest ---


async def test_samples_are_stored_with_their_values(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    ts = iso(timedelta(seconds=-10))
    resp = await post(
        client,
        sample("host-1", "cpu", 97, ts=ts),
        sample("host-1", "mem", 40.5, ts=ts),
        sample("host-2", "cpu", 3, ts=ts),
    )
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 3}

    rows = await db.fetch("SELECT host_id, name, value, ts FROM metrics ORDER BY host_id, name")
    assert [(r["host_id"], r["name"], r["value"]) for r in rows] == [
        ("host-1", "cpu", 97.0),
        ("host-1", "mem", 40.5),
        ("host-2", "cpu", 3.0),
    ]
    assert {r["ts"] for r in rows} == {datetime.fromisoformat(ts)}
    hosts = await db.fetch("SELECT id FROM hosts ORDER BY id")
    assert [r["id"] for r in hosts] == ["host-1", "host-2"]


async def test_missing_ts_defaults_to_now(client: httpx.AsyncClient, db: asyncpg.Pool) -> None:
    await post(client, sample())
    ts = await db.fetchval("SELECT ts FROM metrics")
    assert abs(datetime.now(UTC) - ts) < timedelta(seconds=5)


async def test_repeat_ingest_updates_last_seen_only(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    await post(client, sample())
    first_seen, last_seen = await db.fetchrow("SELECT first_seen, last_seen FROM hosts")
    await post(client, sample())
    first_seen2, last_seen2 = await db.fetchrow("SELECT first_seen, last_seen FROM hosts")
    assert first_seen2 == first_seen
    assert last_seen2 > last_seen


async def test_redis_status_has_latest_value_and_ttl(
    client: httpx.AsyncClient, redis_client: Redis, settings: Settings
) -> None:
    # Newest first in the batch: the newest ts must win, not the last position.
    await post(
        client,
        sample(value=2, ts=iso()),
        sample(value=1, ts=iso(timedelta(seconds=-30))),
    )
    assert await redis_client.hgetall("host:host-1") == {"cpu": "2.0"}
    assert 0 < await redis_client.ttl("host:host-1") <= settings.host_ttl_secs


async def test_partial_batch_keeps_other_metrics(
    client: httpx.AsyncClient, redis_client: Redis
) -> None:
    await post(client, sample(name="cpu", value=1), sample(name="mem", value=2))
    await post(client, sample(name="cpu", value=3))
    assert await redis_client.hgetall("host:host-1") == {"cpu": "3.0", "mem": "2.0"}


async def test_event_is_published(client: httpx.AsyncClient, redis_client: Redis) -> None:
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe("events")
        await pubsub.get_message(timeout=2)  # the subscribe confirmation
        await post(client, sample(value=7))
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert message is not None
    event = json.loads(message["data"])
    assert event["type"] == "metrics"
    assert [(s["host_id"], s["name"], s["value"]) for s in event["samples"]] == [
        ("host-1", "cpu", 7.0)
    ]


async def test_concurrent_overlapping_batches_do_not_deadlock(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    hosts = [f"host-{i}" for i in range(20)]
    forward = [sample(h) for h in hosts]
    backward = list(reversed(forward))
    responses = await asyncio.gather(
        *(post(client, *(forward if i % 2 else backward)) for i in range(20))
    )
    assert [r.status_code for r in responses] == [202] * 20
    assert await db.fetchval("SELECT count(*) FROM metrics") == 400


async def test_redis_down_still_accepts_and_stores(
    settings: Settings, make_client: ClientFactory, db: asyncpg.Pool
) -> None:
    broken = settings.model_copy(update={"redis_url": "redis://localhost:1/0"})
    async with make_client(broken) as c:
        resp = await post(c, sample())
    assert resp.status_code == 202
    assert await db.fetchval("SELECT count(*) FROM metrics") == 1


# --- GET /v1/metrics ---


async def history(client: httpx.AsyncClient, **params: str) -> list[dict]:
    resp = await client.get("/v1/metrics", params=params)
    assert resp.status_code == 200
    return resp.json()["samples"]


async def test_history_filters_and_orders_oldest_first(client: httpx.AsyncClient) -> None:
    await post(
        client,
        sample("host-1", "cpu", 2, ts=iso(timedelta(seconds=-10))),
        sample("host-1", "cpu", 1, ts=iso(timedelta(seconds=-20))),
        sample("host-1", "mem", 50),
        sample("host-2", "cpu", 60),
    )
    samples = await history(client, host_id="host-1", name="cpu")
    assert [s["value"] for s in samples] == [1.0, 2.0]


async def test_history_since_filters_and_is_clamped_to_one_hour(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    await post(client, sample(value=1))
    now = datetime.now(UTC)
    # Old rows can only be inserted directly: ingest rejects them.
    await db.execute(
        "INSERT INTO metrics (host_id, name, value, ts) VALUES ('host-1', 'cpu', 2, $1)",
        now - timedelta(minutes=30),
    )
    await db.execute(
        "INSERT INTO metrics (host_id, name, value, ts) VALUES ('host-1', 'cpu', 3, $1)",
        now - timedelta(hours=2),
    )
    recent = await history(
        client, host_id="host-1", name="cpu", since=(now - timedelta(minutes=5)).isoformat()
    )
    assert [s["value"] for s in recent] == [1.0]
    clamped = await history(
        client, host_id="host-1", name="cpu", since=(now - timedelta(days=1)).isoformat()
    )
    assert [s["value"] for s in clamped] == [2.0, 1.0]


async def test_history_returns_newest_rows_up_to_the_limit(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    await post(client, sample(value=-1))
    now = datetime.now(UTC)
    extra = MAX_HISTORY_ROWS + 5
    await db.execute(
        """
        INSERT INTO metrics (host_id, name, value, ts)
        SELECT 'host-1', 'cpu', g, $1::timestamptz - make_interval(secs => g)
        FROM generate_series(1, $2::int) AS g
        """,
        now,
        extra,
    )
    samples = await history(client, host_id="host-1", name="cpu")
    assert len(samples) == MAX_HISTORY_ROWS
    # The rows dropped are the oldest (highest g), and the newest comes last.
    assert samples[0]["value"] == float(MAX_HISTORY_ROWS - 1)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"name": "cpu"}, id="missing-host"),
        pytest.param({"host_id": "BAD", "name": "cpu"}, id="bad-host"),
        pytest.param({"host_id": "h", "name": "cpu", "since": "2026-01-01T00:00:00"}, id="naive"),
    ],
)
async def test_history_rejects_bad_params(client: httpx.AsyncClient, params: dict) -> None:
    assert (await client.get("/v1/metrics", params=params)).status_code == 422
