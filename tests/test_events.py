"""GET /v1/events against a real uvicorn server, Postgres and Redis.

httpx's in-process transport waits for a response to finish, so it can't read a stream
that never ends. These tests connect to a real server on a free port instead.
"""

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from api import events as events_module
from api.config import Settings
from api.events import Event, EventHub, events_channel
from api.main import create_app
from api.routes import events as events_routes
from tests.conftest import API_KEY, REDIS_URL

HEADERS = {"X-API-Key": API_KEY}
BATCH = {"samples": [{"host_id": "host-1", "name": "cpu", "value": 42}]}


@dataclass
class LiveServer:
    app: FastAPI
    server: uvicorn.Server
    url: str


@pytest.fixture
async def live(settings: Settings, redis_client: Redis) -> AsyncIterator[LiveServer]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    app = create_app(settings)
    config = uvicorn.Config(app, log_config=None, lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        # Wait for the hub's subscription, so tests don't race its startup.
        channel = events_channel(redis_client)
        while (await redis_client.pubsub_numsub(channel))[0][1] < 1:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    yield LiveServer(app, server, url)
    server.should_exit = True
    server.force_exit = True
    await task
    sock.close()


class Stream:
    """Reads server-sent events from an open response."""

    def __init__(self, response: httpx.Response) -> None:
        self.lines = response.aiter_lines()

    async def next_event(self, wait_secs: float = 5.0) -> tuple[str, dict[str, Any]]:
        event = ""
        async with asyncio.timeout(wait_secs):
            async for line in self.lines:
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:") and event:
                    return event, json.loads(line.removeprefix("data:"))
        raise EOFError("the stream ended")


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[Stream]:
    """Opens a stream and consumes the first event, so the subscription is known to be live."""
    async with (
        httpx.AsyncClient(base_url=url, timeout=None) as client,
        client.stream("GET", "/v1/events") as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        stream = Stream(response)
        assert (await stream.next_event())[0] == "resync"
        yield stream


async def wait_for_clients(app: FastAPI, count: int) -> None:
    async with asyncio.timeout(5):
        while len(app.state.hub.clients) != count:  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def post_batch(url: str) -> None:
    async with httpx.AsyncClient(base_url=url) as client:
        response = await client.post("/v1/metrics", headers=HEADERS, json=BATCH)
    assert response.status_code == 202


# --- delivery ---


async def test_metrics_arrive_as_metrics_events(live: LiveServer) -> None:
    async with connect(live.url) as stream:
        await post_batch(live.url)
        event, data = await stream.next_event()
    assert event == "metrics"
    assert [(s["host_id"], s["name"], s["value"]) for s in data["samples"]] == [
        ("host-1", "cpu", 42.0)
    ]


async def test_incident_changes_arrive_as_incident_events(
    live: LiveServer, db: asyncpg.Pool
) -> None:
    await post_batch(live.url)
    incident = await db.fetchval(
        """
        INSERT INTO incidents (host_id, rule_id, message)
        VALUES ('host-1', (SELECT id FROM alert_rules WHERE name = 'High CPU'), 'x')
        RETURNING id
        """
    )
    async with connect(live.url) as stream, httpx.AsyncClient(base_url=live.url) as client:
        await client.post(f"/v1/incidents/{incident}/ack")
        event, data = await stream.next_event()
        assert (event, data["id"], data["status"]) == ("incident", incident, "acknowledged")

        await client.post(f"/v1/incidents/{incident}/resolve")
        event, data = await stream.next_event()
        assert (event, data["id"], data["status"]) == ("incident", incident, "resolved")


async def test_every_client_receives_each_event(live: LiveServer) -> None:
    async with connect(live.url) as first, connect(live.url) as second:
        await post_batch(live.url)
        assert (await first.next_event())[0] == "metrics"
        assert (await second.next_event())[0] == "metrics"


# --- channel scoping ---


def test_channel_names_the_redis_db() -> None:
    assert events_channel(Redis.from_url("redis://localhost:6379/0")) == "events:0"
    assert events_channel(Redis.from_url("redis://localhost:6379/1")) == "events:1"
    assert events_channel(Redis.from_url("redis://localhost:6379")) == "events:0"


async def test_events_from_another_redis_db_are_not_received(live: LiveServer) -> None:
    other_db = "events:99" if events_channel(Redis.from_url(REDIS_URL)) != "events:99" else "x"
    async with connect(live.url) as stream:
        publisher = Redis.from_url(REDIS_URL)
        await publisher.publish(other_db, json.dumps({"type": "metrics", "samples": []}))
        await publisher.aclose()
        await post_batch(live.url)  # only this one should arrive
        _, data = await stream.next_event()
    assert data["samples"] != []


# --- quiet periods ---


async def test_quiet_subscription_is_not_treated_as_a_failure(live: LiveServer) -> None:
    # Longer than the normal Redis client's 2s read timeout. A timeout here would reconnect
    # and send a resync.
    async with connect(live.url) as stream:
        with pytest.raises(TimeoutError):
            await stream.next_event(wait_secs=2.5)


async def test_idle_stream_gets_keep_alive_pings(
    live: LiveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events_routes, "PING_SECS", 0.1)
    async with (
        httpx.AsyncClient(base_url=live.url, timeout=None) as client,
        client.stream("GET", "/v1/events") as response,
    ):
        async with asyncio.timeout(5):
            async for line in response.aiter_lines():
                if line.startswith(": ping"):
                    break


# --- client cleanup ---


async def test_disconnecting_removes_the_client(live: LiveServer) -> None:
    assert len(live.app.state.hub.clients) == 0
    async with connect(live.url), connect(live.url):
        assert len(live.app.state.hub.clients) == 2
    await wait_for_clients(live.app, 0)


async def test_slow_client_stream_ends_and_it_can_reconnect(
    live: LiveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events_module, "MAX_QUEUED_EVENTS", 1)
    hub = live.app.state.hub
    async with connect(live.url) as stream:
        # Two events with no chance for the stream to read between them overflow its queue.
        hub.broadcast(Event("metrics", '{"n": 1}'))
        hub.broadcast(Event("metrics", '{"n": 2}'))
        assert len(hub.clients) == 0
        with pytest.raises(EOFError):
            await stream.next_event()

    async with connect(live.url) as stream:
        await post_batch(live.url)
        assert (await stream.next_event())[0] == "metrics"


def test_only_the_slow_client_is_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_module, "MAX_QUEUED_EVENTS", 2)
    hub = EventHub(REDIS_URL)
    slow = hub.subscribe()
    fast = hub.subscribe()
    fast.get_nowait()  # the first resync

    for n in range(2):
        hub.broadcast(Event("metrics", str(n)))
        fast.get_nowait()

    assert hub.clients == {fast}
    assert slow.get_nowait() is None, "the slow client's stream is told to end"
    assert slow.empty()


# --- Redis trouble ---


async def test_subscription_recovers_and_clients_are_told_to_resync(
    live: LiveServer, redis_client: Redis
) -> None:
    async with connect(live.url) as stream:
        # This also drops any running stack's subscriber, which reconnects the same way.
        await redis_client.execute_command("CLIENT", "KILL", "TYPE", "pubsub")

        event, _ = await stream.next_event(wait_secs=10)
        assert event == "resync"

        await post_batch(live.url)
        event, _ = await stream.next_event(wait_secs=10)
        assert event == "metrics"


async def test_hub_survives_redis_being_down_at_startup() -> None:
    hub = EventHub("redis://localhost:1/0")
    task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.2)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_bad_message_does_not_end_live_updates(
    live: LiveServer, redis_client: Redis
) -> None:
    async with connect(live.url) as stream:
        await redis_client.publish(events_channel(redis_client), "not json")
        event, _ = await stream.next_event(wait_secs=10)
        assert event == "resync"
        await post_batch(live.url)
        assert (await stream.next_event(wait_secs=10))[0] == "metrics"


# --- shutdown ---


async def test_server_stops_promptly_with_a_stream_open(
    settings: Settings, redis_client: Redis
) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_config=None))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    async with connect(f"http://127.0.0.1:{sock.getsockname()[1]}"):
        # What SIGTERM sets. Sending the signal itself would also kill pytest: uvicorn re-raises
        # it after stopping. The real signal is checked against the compose stack.
        server.should_exit = True
        async with asyncio.timeout(5):
            await task
    sock.close()
