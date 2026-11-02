"""GET /v1/hosts: status comes from the Redis TTL."""

import asyncio

import asyncpg
from redis.asyncio import Redis

from api.config import Settings
from tests.conftest import API_KEY, ClientFactory

HEADERS = {"X-API-Key": API_KEY}


def batch(host: str = "host-1") -> dict:
    return {"samples": [{"host_id": host, "name": "cpu", "value": 50}]}


async def test_host_goes_down_when_ttl_expires_and_up_on_next_report(
    settings: Settings, make_client: ClientFactory, redis_client: Redis
) -> None:
    short_ttl = settings.model_copy(update={"host_ttl_secs": 2})
    async with make_client(short_ttl) as client:
        await client.post("/v1/metrics", headers=HEADERS, json=batch())

        host = (await client.get("/v1/hosts")).json()["hosts"][0]
        assert (host["id"], host["status"], host["metrics"]) == ("host-1", "up", {"cpu": 50.0})

        await asyncio.sleep(2.5)
        host = (await client.get("/v1/hosts")).json()["hosts"][0]
        assert (host["id"], host["status"], host["metrics"]) == ("host-1", "down", {})

        await client.post("/v1/metrics", headers=HEADERS, json=batch())
        assert (await client.get("/v1/hosts")).json()["hosts"][0]["status"] == "up"


async def test_empty_when_no_host_has_reported(
    settings: Settings, make_client: ClientFactory, redis_client: Redis
) -> None:
    async with make_client(settings) as client:
        assert (await client.get("/v1/hosts")).json() == {"hosts": []}


async def test_503_when_redis_unreachable(
    settings: Settings, make_client: ClientFactory, db: asyncpg.Pool
) -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ('host-1')")
    broken = settings.model_copy(update={"redis_url": "redis://localhost:1/0"})
    async with make_client(broken) as client:
        assert (await client.get("/v1/hosts")).status_code == 503
