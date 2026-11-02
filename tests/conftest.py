import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from api.config import Settings
from api.main import create_app

# Defaults match `docker compose up -d postgres redis`; CI sets the env vars.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://monitor:monitor@localhost:5432/monitor")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/1")

TEMPLATE_DB = "monitor_test_template"
ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def url_for_database(name: str) -> str:
    return urlsplit(DATABASE_URL)._replace(path=f"/{name}").geturl()


def alembic_config(database_url: str) -> Config:
    config = Config(ALEMBIC_INI)
    config.attributes["database_url"] = database_url
    return config


def admin_execute(statement: sql.Composed) -> None:
    # CREATE/DROP DATABASE can't run inside a transaction, hence autocommit.
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(statement)


def drop_database(name: str) -> None:
    admin_execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    """A database holding exactly what the real migrations produce."""
    drop_database(TEMPLATE_DB)
    admin_execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEMPLATE_DB)))
    command.upgrade(alembic_config(url_for_database(TEMPLATE_DB)), "head")
    yield TEMPLATE_DB
    drop_database(TEMPLATE_DB)


@pytest.fixture
def database_url(template_db: str) -> Iterator[str]:
    """URL of a fresh copy of the template, private to this test."""
    name = f"monitor_test_{uuid.uuid4().hex}"
    admin_execute(
        sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(template_db)
        )
    )
    yield url_for_database(name)
    drop_database(name)


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    yield pool
    await pool.close()


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, redis_url=REDIS_URL)


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
