"""Schema constraints and migrations, checked against real Postgres."""

import asyncpg
import psycopg
import pytest
from alembic import command

from tests.conftest import alembic_config


async def insert_host(db: asyncpg.Pool, host: str = "host-1") -> None:
    await db.execute("INSERT INTO hosts (id) VALUES ($1)", host)


async def rule_id(db: asyncpg.Pool, name: str) -> int:
    return await db.fetchval("SELECT id FROM alert_rules WHERE name = $1", name)


async def open_incident(db: asyncpg.Pool, host: str, rule: int) -> str:
    """The statement the alert engine will use. Returns the command tag, e.g. 'INSERT 0 1'."""
    return await db.execute(
        """
        INSERT INTO incidents (host_id, rule_id, message) VALUES ($1, $2, 'test')
        ON CONFLICT DO NOTHING
        """,
        host,
        rule,
    )


async def test_seeds_host_down_and_high_cpu_rules(db: asyncpg.Pool) -> None:
    rows = await db.fetch(
        """
        SELECT name, kind, metric, op, threshold, duration_secs, cooldown_secs, enabled, builtin
        FROM alert_rules ORDER BY name
        """
    )
    assert [tuple(r) for r in rows] == [
        ("High CPU", "threshold", "cpu", ">", 90.0, 30, 60, True, False),
        ("Host down", "host_down", None, None, None, None, 60, True, True),
    ]


async def test_at_most_one_active_incident_per_host_and_rule(db: asyncpg.Pool) -> None:
    await insert_host(db)
    cpu = await rule_id(db, "High CPU")

    assert await open_incident(db, "host-1", cpu) == "INSERT 0 1"
    assert await open_incident(db, "host-1", cpu) == "INSERT 0 0", "duplicate of open"

    # Acknowledged is still active, so it must also block a new incident.
    await db.execute("UPDATE incidents SET status = 'acknowledged', acknowledged_at = now()")
    assert await open_incident(db, "host-1", cpu) == "INSERT 0 0", "duplicate of acknowledged"

    # Once resolved, a new incident for the same host+rule is allowed.
    await db.execute("UPDATE incidents SET status = 'resolved', resolved_at = now()")
    assert await open_incident(db, "host-1", cpu) == "INSERT 0 1", "new after resolve"

    # A different rule on the same host is independent.
    down = await rule_id(db, "Host down")
    assert await open_incident(db, "host-1", down) == "INSERT 0 1", "other rule"


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param(
            "INSERT INTO alert_rules (name, kind, metric) VALUES ('x', 'threshold', 'cpu')",
            id="threshold-missing-fields",
        ),
        pytest.param(
            "INSERT INTO alert_rules (name, kind, metric) VALUES ('x', 'host_down', 'cpu')",
            id="host-down-with-metric",
        ),
    ],
)
async def test_rule_fields_must_match_kind(db: asyncpg.Pool, statement: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="alert_rules_kind_fields"):
        await db.execute(statement)


async def test_only_one_host_down_rule(db: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.UniqueViolationError, match="alert_rules_one_host_down"):
        await db.execute("INSERT INTO alert_rules (name, kind) VALUES ('Second', 'host_down')")


async def test_resolved_status_requires_resolved_at(db: asyncpg.Pool) -> None:
    await insert_host(db)
    await open_incident(db, "host-1", await rule_id(db, "High CPU"))
    with pytest.raises(asyncpg.CheckViolationError, match="incidents_resolved_has_time"):
        await db.execute("UPDATE incidents SET status = 'resolved'")


async def test_acknowledged_status_requires_acknowledged_at(db: asyncpg.Pool) -> None:
    await insert_host(db)
    await open_incident(db, "host-1", await rule_id(db, "High CPU"))
    with pytest.raises(asyncpg.CheckViolationError, match="incidents_ack_has_time"):
        await db.execute("UPDATE incidents SET status = 'acknowledged'")


async def test_rule_with_incident_history_cannot_be_deleted(db: asyncpg.Pool) -> None:
    await insert_host(db)
    cpu = await rule_id(db, "High CPU")
    await open_incident(db, "host-1", cpu)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("DELETE FROM alert_rules WHERE id = $1", cpu)


def public_tables(database_url: str) -> set[str]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row[0] for row in rows} - {"alembic_version"}


def test_migration_downgrades_and_upgrades_again(database_url: str) -> None:
    tables = {"hosts", "metrics", "alert_rules", "incidents"}
    config = alembic_config(database_url)
    assert public_tables(database_url) == tables

    command.downgrade(config, "base")
    assert public_tables(database_url) == set()

    command.upgrade(config, "head")
    assert public_tables(database_url) == tables
