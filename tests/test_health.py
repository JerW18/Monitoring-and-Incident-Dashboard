from api.config import Settings
from tests.conftest import ClientFactory


async def test_healthz_ok_with_real_postgres_and_redis(
    settings: Settings, make_client: ClientFactory
) -> None:
    async with make_client(settings) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"db": "ok", "redis": "ok"}


async def test_healthz_503_when_redis_unreachable(
    settings: Settings, make_client: ClientFactory
) -> None:
    # Nothing listens on port 1, so the Redis PING fails.
    broken = settings.model_copy(update={"redis_url": "redis://localhost:1/0"})
    async with make_client(broken) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"db": "ok", "redis": "error"}
