import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from redis.asyncio import Redis

from api.config import Settings
from api.routes import health


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with `uvicorn --factory api.main:create_app`."""
    settings = settings or Settings()  # type: ignore[call-arg]  # fields come from env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.pool.close()

    app = FastAPI(title="Monitoring API", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    return app
