"""The incident endpoints against real Postgres and Redis."""

from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from api.alerts.engine import evaluate_once
from api.config import Settings
from api.routes.incidents import MAX_INCIDENTS
from tests.conftest import ClientFactory
from tests.test_engine import BAD, add_samples, incidents


@pytest.fixture
async def client(
    settings: Settings, make_client: ClientFactory, redis_client: Redis
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(settings) as c:
        yield c


@pytest.fixture
async def incident(db: asyncpg.Pool) -> int:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-1')")
    return await db.fetchval(
        """
        INSERT INTO incidents (host_id, rule_id, message)
        VALUES ('host-1', (SELECT id FROM alert_rules WHERE name = 'High CPU'), 'cpu high')
        RETURNING id
        """
    )


async def get_incidents(client: httpx.AsyncClient, **params: str) -> list[dict]:
    response = await client.get("/v1/incidents", params=params)
    assert response.status_code == 200
    return response.json()["incidents"]


# --- list ---


async def test_list_includes_rule_name_and_details(
    client: httpx.AsyncClient, incident: int
) -> None:
    rows = await get_incidents(client)
    assert len(rows) == 1
    row = rows[0]
    assert (row["id"], row["host_id"], row["status"]) == (incident, "host-1", "open")
    assert (row["rule_name"], row["message"]) == ("High CPU", "cpu high")
    assert row["opened_at"] is not None


async def test_status_filter(client: httpx.AsyncClient, db: asyncpg.Pool, incident: int) -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-2')")
    second = await db.fetchval(
        """
        INSERT INTO incidents (host_id, rule_id, message)
        VALUES ('host-2', (SELECT id FROM alert_rules WHERE name = 'High CPU'), 'x')
        RETURNING id
        """
    )
    assert (await client.post(f"/v1/incidents/{second}/resolve")).status_code == 200

    assert [r["id"] for r in await get_incidents(client, status="open")] == [incident]
    assert [r["id"] for r in await get_incidents(client, status="resolved")] == [second]
    assert await get_incidents(client, status="acknowledged") == []
    assert {r["id"] for r in await get_incidents(client)} == {incident, second}


async def test_unknown_status_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/incidents", params={"status": "nope"})).status_code == 422


async def test_list_is_newest_first_and_limited(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-1')")
    total = MAX_INCIDENTS + 5
    await db.execute(
        """
        INSERT INTO incidents (host_id, rule_id, message, status, resolved_at, opened_at)
        SELECT 'host-1', (SELECT id FROM alert_rules WHERE name = 'High CPU'), g::text,
               'resolved', now(), now() - make_interval(secs => g)
        FROM generate_series(1, $1::int) AS g
        """,
        total,
    )
    rows = await get_incidents(client)
    assert len(rows) == MAX_INCIDENTS
    # The message holds the age in seconds: newest first, and the oldest are the ones dropped.
    assert [int(r["message"]) for r in rows] == list(range(1, MAX_INCIDENTS + 1))


# --- ack and resolve ---


async def test_ack_returns_the_updated_incident(client: httpx.AsyncClient, incident: int) -> None:
    response = await client.post(f"/v1/incidents/{incident}/ack")
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"]) == (incident, "acknowledged")
    assert body["acknowledged_at"] is not None
    assert body["resolved_at"] is None


async def test_resolve_returns_the_updated_incident(
    client: httpx.AsyncClient, incident: int
) -> None:
    response = await client.post(f"/v1/incidents/{incident}/resolve")
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"]) == (incident, "resolved")
    assert body["resolved_at"] is not None


async def test_second_ack_is_a_conflict(client: httpx.AsyncClient, incident: int) -> None:
    await client.post(f"/v1/incidents/{incident}/ack")
    response = await client.post(f"/v1/incidents/{incident}/ack")
    assert response.status_code == 409
    assert "acknowledged" in response.json()["detail"]


@pytest.mark.parametrize("action", ["ack", "resolve"])
async def test_resolved_incident_cannot_change_and_stays_as_it_was(
    client: httpx.AsyncClient, incident: int, action: str
) -> None:
    resolved = (await client.post(f"/v1/incidents/{incident}/resolve")).json()
    response = await client.post(f"/v1/incidents/{incident}/{action}")
    assert response.status_code == 409

    row = (await get_incidents(client))[0]
    assert row["status"] == "resolved"
    assert row["resolved_at"] == resolved["resolved_at"]
    assert row["acknowledged_at"] is None


@pytest.mark.parametrize("action", ["ack", "resolve"])
async def test_unknown_incident_is_not_found(client: httpx.AsyncClient, action: str) -> None:
    assert (await client.post(f"/v1/incidents/999/{action}")).status_code == 404


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc", str(2**63)])
async def test_bad_incident_id_is_rejected(client: httpx.AsyncClient, bad_id: str) -> None:
    assert (await client.post(f"/v1/incidents/{bad_id}/ack")).status_code == 422


# --- with the engine ---


async def test_resolving_through_the_api_is_not_reopened_by_the_next_tick(
    client: httpx.AsyncClient, db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    incident_id = (await incidents(db))[0]["id"]

    assert (await client.post(f"/v1/incidents/{incident_id}/resolve")).status_code == 200

    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]


async def test_acknowledged_incident_blocks_a_duplicate(
    client: httpx.AsyncClient, db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, BAD)
    await evaluate_once(db, redis_client, settings)
    incident_id = (await incidents(db))[0]["id"]
    await client.post(f"/v1/incidents/{incident_id}/ack")

    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["acknowledged"]
