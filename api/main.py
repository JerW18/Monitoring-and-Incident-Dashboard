import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from redis.asyncio import Redis

from api.config import Settings
from api.routes import health, hosts, metrics

# A hung Redis must not hang request handling.
REDIS_TIMEOUT_SECS = 2.0


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with `uvicorn --factory api.main:create_app`."""
    settings = settings or Settings()  # type: ignore[call-arg]  # fields come from env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        app.state.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=REDIS_TIMEOUT_SECS,
            socket_connect_timeout=REDIS_TIMEOUT_SECS,
        )
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.pool.close()

    app = FastAPI(title="Monitoring API", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(hosts.router)
    return app
