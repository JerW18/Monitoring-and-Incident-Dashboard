"""The whole product at once, with nothing called directly and the engine running by itself."""

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from api.alerts.engine import evaluate_once
from api.config import Settings
from api.main import create_app
from tests.conftest import API_KEY, ClientFactory
from tests.test_engine import add_samples, incidents
from tests.test_events import LiveServer, Stream, connect, serve

HEADERS = {"X-API-Key": API_KEY}
REPO_ROOT = Path(__file__).parent.parent


def history(value: float, oldest: int, newest: int = 0) -> dict[str, list[dict[str, Any]]]:
    """A batch of cpu samples, one every 2s. Ingest accepts timestamps up to 5 minutes old."""
    now = datetime.now(UTC)
    return {
        "samples": [
            {
                "host_id": "host-1",
                "name": "cpu",
                "value": value,
                "ts": (now - timedelta(seconds=ago)).isoformat(),
            }
            for ago in range(oldest, newest - 1, -2)
        ]
    }


async def next_incident_event(stream: Stream) -> dict[str, Any]:
    """Skips the metrics events, which aren't what these tests are waiting for."""
    while True:
        event, data = await stream.next_event(wait_secs=10)
        if event == "incident":
            return data


@pytest.fixture
async def engine_running(settings: Settings, redis_client: Redis) -> AsyncIterator[LiveServer]:
    fast = settings.model_copy(update={"alert_engine_enabled": True, "alert_interval_secs": 0.1})
    async with serve(fast, redis_client) as server:
        yield server


async def test_breach_to_incident_to_ack_to_resolve(engine_running: LiveServer) -> None:
    url = engine_running.url
    async with connect(url) as stream, httpx.AsyncClient(base_url=url) as client:
        # A sustained breach arrives over HTTP, and the engine notices by itself.
        response = await client.post("/v1/metrics", headers=HEADERS, json=history(97, oldest=40))
        assert response.status_code == 202

        opened = await next_incident_event(stream)
        assert (opened["host_id"], opened["status"]) == ("host-1", "open")
        incident_id = opened["id"]

        listed = (await client.get("/v1/incidents")).json()["incidents"]
        assert [(i["id"], i["rule_name"], i["status"]) for i in listed] == [
            (incident_id, "High CPU", "open")
        ]

        # A person acknowledges it, and every connected client hears about it.
        assert (await client.post(f"/v1/incidents/{incident_id}/ack")).status_code == 200
        event = await next_incident_event(stream)
        assert (event["id"], event["status"]) == (incident_id, "acknowledged")

        # Then resolves it, while the host is still breaching.
        assert (await client.post(f"/v1/incidents/{incident_id}/resolve")).status_code == 200
        event = await next_incident_event(stream)
        assert (event["id"], event["status"]) == (incident_id, "resolved")

        # The engine keeps running, and the cooldown stops it from reopening.
        await asyncio.sleep(0.5)
        statuses = [i["status"] for i in (await client.get("/v1/incidents")).json()["incidents"]]
        assert statuses == ["resolved"]


async def test_breach_then_recovery_resolves_by_itself(engine_running: LiveServer) -> None:
    url = engine_running.url
    async with connect(url) as stream, httpx.AsyncClient(base_url=url) as client:
        await client.post("/v1/metrics", headers=HEADERS, json=history(97, oldest=40))
        opened = await next_incident_event(stream)

        await client.post("/v1/metrics", headers=HEADERS, json=history(10, oldest=0))

        event = await next_incident_event(stream)
        assert (event["id"], event["status"]) == (opened["id"], "resolved")


async def test_every_redis_key_has_a_ttl(
    db: asyncpg.Pool, redis_client: Redis, settings: Settings, make_client: ClientFactory
) -> None:
    """CLAUDE.md: every Redis key gets a TTL. This does the real work, then checks them all."""
    async with make_client(settings) as client:
        batch = {"samples": [{"host_id": "host-2", "name": "cpu", "value": 1}]}
        await client.post("/v1/metrics", headers=HEADERS, json=batch)

        await add_samples(db, 97.0)
        await evaluate_once(db, redis_client, settings)
        incident_id = (await incidents(db))[0]["id"]
        await client.post(f"/v1/incidents/{incident_id}/resolve")

    keys = await redis_client.keys("*")
    assert {str(key).split(":")[0] for key in keys} == {"host", "cooldown"}, (
        "exercise every kind of key"
    )
    # ttl is -1 for a key with no expiry.
    ttls = {key: await redis_client.ttl(key) for key in keys}
    assert all(ttl > 0 for ttl in ttls.values()), ttls


# --- Postgres unavailable ---


class PostgresDown:
    """Stands in for the pool: every call fails the way a real outage does."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        def fail(*args: object, **kwargs: object) -> NoReturn:
            raise self.error

        return fail


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(socket.gaierror(-2, "Name or service not known"), id="name-not-found"),
        pytest.param(ConnectionRefusedError("refused"), id="connection-refused"),
        pytest.param(TimeoutError("timed out"), id="timed-out"),
        pytest.param(asyncpg.CannotConnectNowError("shutting down"), id="shutting-down"),
        pytest.param(asyncpg.ConnectionDoesNotExistError("gone"), id="connection-lost"),
    ],
)
@pytest.mark.parametrize(
    "method, path, body",
    [
        # No ts: a timestamp worked out at collection could be too old by the time this runs.
        pytest.param(
            "POST",
            "/v1/metrics",
            {"samples": [{"host_id": "h", "name": "cpu", "value": 1}]},
            id="ingest",
        ),
        pytest.param("GET", "/v1/hosts", None, id="hosts"),
        pytest.param("GET", "/v1/incidents", None, id="incidents"),
        pytest.param("POST", "/v1/incidents/1/ack", None, id="ack"),
        pytest.param("GET", "/v1/rules", None, id="rules"),
    ],
)
async def test_postgres_outage_is_a_503_not_a_500(
    settings: Settings,
    redis_client: Redis,
    error: Exception,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        real_pool = app.state.pool
        app.state.pool = PostgresDown(error)
        # raise_app_exceptions=False: see what a client sees, as a real server would send it.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, headers=HEADERS, json=body)
        app.state.pool = real_pool
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


async def test_a_query_bug_is_not_reported_as_an_outage(
    settings: Settings, redis_client: Redis
) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        real_pool = app.state.pool
        app.state.pool = PostgresDown(asyncpg.DataError("invalid input"))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/rules")
        app.state.pool = real_pool
    assert response.status_code == 500


# --- python -m agent, as a real process ---


def agent_env() -> dict[str, str]:
    env = {**os.environ, "API_KEY": API_KEY}
    env.pop("API_URL", None)
    return env


async def test_agent_process_reports_until_told_to_stop(
    live_for_agent: LiveServer, db: asyncpg.Pool
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agent",
        "--api-url",
        live_for_agent.url,
        "--hosts",
        "2",
        "--interval",
        "0.05",
        "--stop",
        "host-1",
        "--stop",
        "host-2",
        "--stop-after",
        "0.3",
        cwd=REPO_ROOT,
        env=agent_env(),
    )
    # It exits by itself once every host has stopped.
    async with asyncio.timeout(20):
        assert await process.wait() == 0

    rows = await db.fetch("SELECT DISTINCT host_id, name FROM metrics ORDER BY host_id, name")
    assert [(r["host_id"], r["name"]) for r in rows] == [
        (host, metric) for host in ("host-1", "host-2") for metric in ("cpu", "disk", "mem")
    ]


@pytest.fixture
async def live_for_agent(settings: Settings, redis_client: Redis) -> AsyncIterator[LiveServer]:
    async with serve(settings, redis_client) as server:
        yield server


@pytest.mark.parametrize(
    "args, message",
    [
        pytest.param(["--spike-cpu", "host-9"], "unknown hosts", id="unknown-host"),
        pytest.param(["--hosts", "0"], "--hosts must be", id="no-hosts"),
    ],
)
def test_agent_process_rejects_bad_flags(args: list[str], message: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent", *args],
        cwd=REPO_ROOT,
        env=agent_env(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert message in result.stderr
