import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest

from api.config import Settings
from api.main import create_app

# Defaults match `docker compose up -d postgres redis`; CI sets the env vars.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://monitor:monitor@localhost:5432/monitor")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/1")

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=DATABASE_URL, redis_url=REDIS_URL)


@pytest.fixture
def make_client() -> ClientFactory:
    """Build the app with the given settings, run its lifespan, and yield an HTTP client."""

    @asynccontextmanager
    async def factory(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return factory
