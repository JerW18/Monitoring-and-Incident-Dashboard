"""The incident state machine. This is the only place status transitions are defined."""

import logging
from enum import StrEnum
from typing import Any

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import RedisError

from api.events import publish_event

log = logging.getLogger(__name__)


class Status(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


ALLOWED_TRANSITIONS = {
    (Status.OPEN, Status.ACKNOWLEDGED),
    (Status.OPEN, Status.RESOLVED),
    (Status.ACKNOWLEDGED, Status.RESOLVED),
}


class IncidentNotFound(Exception):
    def __init__(self, incident_id: int) -> None:
        super().__init__(f"incident {incident_id} not found")


class InvalidTransition(Exception):
    def __init__(self, current: Status, target: Status) -> None:
        super().__init__(f"cannot move incident from {current} to {target}")


def check_transition(current: Status, target: Status) -> None:
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(current, target)


def cooldown_key(rule_id: int, host_id: str) -> str:
    return f"cooldown:{rule_id}:{host_id}"


async def incident_event(redis: Redis, incident: Any) -> None:
    await publish_event(
        redis,
        {
            "type": "incident",
            "id": incident["id"],
            "host_id": incident["host_id"],
            "rule_id": incident["rule_id"],
            "status": incident["status"],
        },
    )


async def _transition(pool: asyncpg.Pool, incident_id: int, target: Status) -> Any:
    # Compare-and-swap: a concurrent change makes the UPDATE match nothing, and the loop
    # re-checks against the new status. Status only moves forward, so this terminates.
    while True:
        current = await pool.fetchval("SELECT status FROM incidents WHERE id = $1", incident_id)
        if current is None:
            raise IncidentNotFound(incident_id)
        check_transition(Status(current), target)
        incident = await pool.fetchrow(
            """
            UPDATE incidents SET
                status = $3::text,
                acknowledged_at = CASE WHEN $3::text = 'acknowledged' THEN now()
                                       ELSE acknowledged_at END,
                resolved_at = CASE WHEN $3::text = 'resolved' THEN now() ELSE resolved_at END
            WHERE id = $1 AND status = $2
            RETURNING *
            """,
            incident_id,
            current,
            target.value,
        )
        if incident is not None:
            return incident


async def acknowledge(pool: asyncpg.Pool, redis: Redis, incident_id: int) -> Any:
    incident = await _transition(pool, incident_id, Status.ACKNOWLEDGED)
    await incident_event(redis, incident)
    return incident


async def resolve(pool: asyncpg.Pool, redis: Redis, incident_id: int) -> Any:
    row = await pool.fetchrow(
        """
        SELECT i.status, i.host_id, i.rule_id, r.cooldown_secs
        FROM incidents i JOIN alert_rules r ON r.id = i.rule_id
        WHERE i.id = $1
        """,
        incident_id,
    )
    if row is None:
        raise IncidentNotFound(incident_id)
    check_transition(Status(row["status"]), Status.RESOLVED)

    # Set before the status changes, so an engine tick running right now can't reopen it.
    if row["cooldown_secs"] > 0:  # Redis rejects EX 0
        try:
            await redis.set(
                cooldown_key(row["rule_id"], row["host_id"]), 1, ex=row["cooldown_secs"]
            )
        except RedisError:
            # Postgres is the source of truth: a person can still resolve during a Redis outage.
            log.exception("failed to set cooldown for incident %s", incident_id)

    incident = await _transition(pool, incident_id, Status.RESOLVED)
    await incident_event(redis, incident)
    return incident
