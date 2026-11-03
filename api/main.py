import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import asyncpg
from fastapi import FastAPI
from redis.asyncio import Redis

from api.alerts.engine import run_engine
from api.config import Settings
from api.errors import register_exception_handlers
from api.events import EventHub
from api.routes import events, health, hosts, incidents, metrics, rules

# A hung Redis must not hang request handling.
REDIS_TIMEOUT_SECS = 2.0


def create_redis(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT_SECS,
        socket_connect_timeout=REDIS_TIMEOUT_SECS,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with `uvicorn --factory api.main:create_app`."""
    settings = settings or Settings()  # type: ignore[call-arg]  # fields come from env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        app.state.redis = create_redis(settings.redis_url)
        app.state.hub = EventHub(settings.redis_url)
        tasks = [asyncio.create_task(app.state.hub.run())]
        if settings.alert_engine_enabled:
            tasks.append(asyncio.create_task(run_engine(app.state.pool, app.state.redis, settings)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await app.state.redis.aclose()
            await app.state.pool.close()

    app = FastAPI(title="Monitoring API", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(hosts.router)
    app.include_router(incidents.router)
    app.include_router(rules.router)
    app.include_router(events.router)
    register_exception_handlers(app)
    return app
