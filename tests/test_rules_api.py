"""The rules endpoints against real Postgres and Redis."""

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from api.alerts.engine import evaluate_once
from api.config import Settings
from tests.conftest import ClientFactory
from tests.test_engine import add_samples, incidents, rule_id


@pytest.fixture
async def client(
    settings: Settings, make_client: ClientFactory, redis_client: Redis
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(settings) as c:
        yield c


def rule_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "name": "Low disk",
        "metric": "disk",
        "op": "<",
        "threshold": 10,
        "duration_secs": 60,
    }
    return {**body, **overrides}


async def rule_names(client: httpx.AsyncClient) -> list[str]:
    return [r["name"] for r in (await client.get("/v1/rules")).json()["rules"]]


# --- list and create ---


async def test_list_has_the_seeded_rules(client: httpx.AsyncClient, db: asyncpg.Pool) -> None:
    assert await rule_names(client) == ["Host down", "High CPU"]


async def test_create_returns_the_rule_with_defaults(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/rules", json=rule_body())
    assert response.status_code == 201
    body = response.json()
    assert body["id"] is not None
    assert (body["kind"], body["metric"], body["op"]) == ("threshold", "disk", "<")
    assert (body["threshold"], body["duration_secs"]) == (10.0, 60)
    assert (body["cooldown_secs"], body["enabled"], body["builtin"]) == (60, True, False)
    assert await rule_names(client) == ["Host down", "High CPU", "Low disk"]


async def test_name_is_trimmed(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/rules", json=rule_body(name="  Low disk  "))
    assert response.json()["name"] == "Low disk"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param({"name": "n" * 65}, id="long-name"),
        pytest.param({"name": 5}, id="numeric-name"),
        pytest.param({"metric": None}, id="no-metric"),
        pytest.param({"metric": "BAD METRIC"}, id="bad-metric"),
        pytest.param({"metric": "cpu\n"}, id="metric-trailing-newline"),
        pytest.param({"op": None}, id="no-op"),
        pytest.param({"op": ">="}, id="bad-op"),
        pytest.param({"threshold": None}, id="no-threshold"),
        pytest.param({"threshold": "90"}, id="string-threshold"),
        pytest.param({"threshold": True}, id="bool-threshold"),
        pytest.param({"duration_secs": None}, id="no-duration"),
        pytest.param({"duration_secs": 0}, id="zero-duration"),
        pytest.param({"duration_secs": 3601}, id="long-duration"),
        pytest.param({"duration_secs": "60"}, id="string-duration"),
        pytest.param({"duration_secs": 1.5}, id="fractional-duration"),
        pytest.param({"cooldown_secs": -1}, id="negative-cooldown"),
        pytest.param({"cooldown_secs": 86401}, id="long-cooldown"),
        pytest.param({"enabled": "yes"}, id="string-enabled"),
        pytest.param({"kind": "host_down"}, id="kind-not-settable"),
        pytest.param({"builtin": True}, id="builtin-not-settable"),
    ],
)
async def test_invalid_rule_is_rejected_and_nothing_is_stored(
    client: httpx.AsyncClient, overrides: dict
) -> None:
    assert (await client.post("/v1/rules", json=rule_body(**overrides))).status_code == 422
    assert await rule_names(client) == ["Host down", "High CPU"]


@pytest.mark.parametrize("literal", [b"1e999", b"NaN", b"Infinity"])
async def test_non_finite_threshold_is_rejected(client: httpx.AsyncClient, literal: bytes) -> None:
    payload = (
        b'{"name": "x", "metric": "cpu", "op": ">", "duration_secs": 5, "threshold": '
        + literal
        + b"}"
    )
    response = await client.post(
        "/v1/rules", content=payload, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [{"duration_secs": 1}, {"duration_secs": 3600}, {"cooldown_secs": 0}, {"cooldown_secs": 86400}],
)
async def test_limits_are_inclusive(client: httpx.AsyncClient, overrides: dict) -> None:
    assert (await client.post("/v1/rules", json=rule_body(**overrides))).status_code == 201


async def test_duplicate_name_is_a_conflict_on_create(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/rules", json=rule_body(name="High CPU"))
    assert response.status_code == 409
    assert "High CPU" in response.json()["detail"]


# --- update ---


async def test_update_replaces_the_rule(client: httpx.AsyncClient, db: asyncpg.Pool) -> None:
    cpu = await rule_id(db)
    response = await client.put(
        f"/v1/rules/{cpu}",
        json=rule_body(name="CPU", metric="cpu", op=">", threshold=80, enabled=False),
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["name"], body["threshold"]) == (cpu, "CPU", 80.0)
    assert body["enabled"] is False
    assert await rule_names(client) == ["Host down", "CPU"]


async def test_duplicate_name_is_a_conflict_on_update(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    cpu = await rule_id(db)
    response = await client.put(f"/v1/rules/{cpu}", json=rule_body(name="Host down"))
    assert response.status_code == 409


async def test_keeping_the_same_name_is_not_a_conflict(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    cpu = await rule_id(db)
    response = await client.put(f"/v1/rules/{cpu}", json=rule_body(name="High CPU"))
    assert response.status_code == 200


async def test_threshold_rule_update_needs_all_fields(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    cpu = await rule_id(db)
    response = await client.put(f"/v1/rules/{cpu}", json={"name": "High CPU", "enabled": False})
    assert response.status_code == 422
    row = await db.fetchrow("SELECT enabled, metric FROM alert_rules WHERE id = $1", cpu)
    assert (row["enabled"], row["metric"]) == (True, "cpu")


async def test_update_unknown_rule_is_not_found(client: httpx.AsyncClient) -> None:
    assert (await client.put("/v1/rules/999", json=rule_body())).status_code == 404


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc", str(2**31)])
async def test_bad_rule_id_is_rejected(client: httpx.AsyncClient, bad_id: str) -> None:
    assert (await client.put(f"/v1/rules/{bad_id}", json=rule_body())).status_code == 422
    assert (await client.delete(f"/v1/rules/{bad_id}")).status_code == 422


# --- the built-in rule ---


async def test_host_down_rule_can_be_tuned_and_disabled(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    down = await rule_id(db, "Host down")
    response = await client.put(
        f"/v1/rules/{down}", json={"name": "Host down", "cooldown_secs": 5, "enabled": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["cooldown_secs"], body["enabled"], body["kind"]) == (5, False, "host_down")


@pytest.mark.parametrize(
    "field",
    [{"metric": "cpu"}, {"op": ">"}, {"threshold": 90}, {"duration_secs": 30}],
    ids=["metric", "op", "threshold", "duration"],
)
async def test_host_down_rule_cannot_become_something_else(
    client: httpx.AsyncClient, db: asyncpg.Pool, field: dict
) -> None:
    down = await rule_id(db, "Host down")
    response = await client.put(f"/v1/rules/{down}", json={"name": "Host down", **field})
    assert response.status_code == 422


async def test_host_down_rule_cannot_be_deleted(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    down = await rule_id(db, "Host down")
    response = await client.delete(f"/v1/rules/{down}")
    assert response.status_code == 409
    assert "built-in" in response.json()["detail"]
    assert "Host down" in await rule_names(client)


# --- delete ---


async def test_delete_removes_the_rule(client: httpx.AsyncClient) -> None:
    created = (await client.post("/v1/rules", json=rule_body())).json()
    response = await client.delete(f"/v1/rules/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert await rule_names(client) == ["Host down", "High CPU"]


async def test_delete_unknown_rule_is_not_found(client: httpx.AsyncClient) -> None:
    assert (await client.delete("/v1/rules/999")).status_code == 404


async def test_rule_with_incident_history_cannot_be_deleted(
    client: httpx.AsyncClient, db: asyncpg.Pool
) -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-1')")
    cpu = await rule_id(db)
    await db.execute(
        """
        INSERT INTO incidents (host_id, rule_id, message, status, resolved_at)
        VALUES ('host-1', $1, 'old', 'resolved', now())
        """,
        cpu,
    )
    response = await client.delete(f"/v1/rules/{cpu}")
    assert response.status_code == 409
    assert "history" in response.json()["detail"]
    assert "High CPU" in await rule_names(client)


# --- rules actually drive the engine ---


async def test_created_rule_fires_and_disabling_it_stops_new_incidents(
    client: httpx.AsyncClient, db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    created = (
        await client.post(
            "/v1/rules",
            json=rule_body(name="Very busy", metric="cpu", op=">", threshold=5, duration_secs=20),
        )
    ).json()
    await add_samples(db, 50.0)  # below the seeded rule's 90, above this rule's 5

    await evaluate_once(db, redis_client, settings)
    rows = await incidents(db)
    assert [(r["rule"], r["message"]) for r in rows] == [("Very busy", "cpu > 5 for 20s")]

    # Disabled: the existing incident is resolved by hand and no new one opens.
    await client.put(
        f"/v1/rules/{created['id']}",
        json=rule_body(
            name="Very busy",
            metric="cpu",
            op=">",
            threshold=5,
            duration_secs=20,
            cooldown_secs=0,
            enabled=False,
        ),
    )
    await client.post(f"/v1/incidents/{rows[0]['id']}/resolve")
    await evaluate_once(db, redis_client, settings)
    assert [r["status"] for r in await incidents(db)] == ["resolved"]


async def test_editing_a_threshold_takes_effect_on_the_next_tick(
    client: httpx.AsyncClient, db: asyncpg.Pool, redis_client: Redis, settings: Settings
) -> None:
    await add_samples(db, 50.0)
    await evaluate_once(db, redis_client, settings)
    assert await incidents(db) == []

    cpu = await rule_id(db)
    await client.put(
        f"/v1/rules/{cpu}",
        json=rule_body(name="High CPU", metric="cpu", op=">", threshold=40, duration_secs=30),
    )
    await evaluate_once(db, redis_client, settings)
    assert [r["message"] for r in await incidents(db)] == ["cpu > 40 for 30s"]
