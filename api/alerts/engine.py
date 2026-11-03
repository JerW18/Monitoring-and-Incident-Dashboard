"""The background task that turns metrics into incidents and resolves them on recovery."""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from redis.asyncio import Redis

from api.alerts.evaluate import MAX_GAP, Sample, breaches, condition_held
from api.config import Settings
from api.incidents import (
    IncidentNotFound,
    InvalidTransition,
    cooldown_key,
    incident_event,
    resolve,
)

log = logging.getLogger(__name__)


async def _hosts_with_status_key(redis: Redis, host_ids: list[str]) -> set[str]:
    pipe = redis.pipeline(transaction=False)
    for host_id in host_ids:
        pipe.exists(f"host:{host_id}")
    found = await pipe.execute()
    return {host_id for host_id, exists in zip(host_ids, found, strict=True) if exists}


async def _open_incident(
    pool: asyncpg.Pool, redis: Redis, rule_id: int, host_id: str, message: str
) -> None:
    if await redis.exists(cooldown_key(rule_id, host_id)):
        return
    # The partial unique index makes this idempotent, however many evaluators run at once.
    incident = await pool.fetchrow(
        """
        INSERT INTO incidents (host_id, rule_id, message) VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        RETURNING id, host_id, rule_id, status
        """,
        host_id,
        rule_id,
        message,
    )
    if incident is not None:
        log.info("opened incident %s: %s: %s", incident["id"], host_id, message)
        await incident_event(redis, incident)


async def _resolve_recovered(pool: asyncpg.Pool, redis: Redis, hosts_up: set[str]) -> None:
    # No enabled filter: a disabled rule opens nothing new, but its incidents can still clear.
    incidents = await pool.fetch(
        """
        SELECT i.id, i.host_id, r.kind, r.op, r.threshold, latest.value AS latest_value
        FROM incidents i
        JOIN alert_rules r ON r.id = i.rule_id
        LEFT JOIN LATERAL (
            SELECT m.value FROM metrics m
            WHERE m.host_id = i.host_id AND m.name = r.metric
            ORDER BY m.ts DESC LIMIT 1
        ) latest ON TRUE
        WHERE i.status IN ('open', 'acknowledged')
        """
    )
    for incident in incidents:
        if incident["kind"] == "host_down":
            recovered = incident["host_id"] in hosts_up
        else:
            # The newest sample may be old if the host stopped reporting. If it still
            # breaches the incident stays open, because recovery is unknown.
            latest = incident["latest_value"]
            recovered = latest is not None and not breaches(
                latest, incident["op"], incident["threshold"]
            )
        if recovered:
            await _resolve_quietly(pool, redis, incident["id"], incident["host_id"])


async def _resolve_quietly(pool: asyncpg.Pool, redis: Redis, incident_id: int, host: str) -> None:
    try:
        await resolve(pool, redis, incident_id)
    except (InvalidTransition, IncidentNotFound) as e:
        # A person resolved it between our read and now, which is the outcome we wanted.
        log.info("incident %s already handled: %s", incident_id, e)
    else:
        log.info("auto-resolved incident %s on %s", incident_id, host)


async def _open_threshold_incidents(
    pool: asyncpg.Pool, redis: Redis, rule: Any, now: datetime
) -> None:
    duration = timedelta(seconds=rule["duration_secs"])
    # LATERAL per host, so the query uses the (host_id, name, ts) index however large metrics grows.
    rows = await pool.fetch(
        """
        SELECT h.id AS host_id, m.ts, m.value
        FROM hosts h
        CROSS JOIN LATERAL (
            SELECT ts, value FROM metrics
            WHERE host_id = h.id AND name = $1 AND ts >= $2 AND ts <= $3
        ) m
        """,
        rule["metric"],
        now - duration - MAX_GAP,
        now,
    )
    samples: dict[str, list[Sample]] = defaultdict(list)
    for row in rows:
        samples[row["host_id"]].append(Sample(row["ts"], row["value"]))

    message = f"{rule['metric']} {rule['op']} {rule['threshold']:g} for {rule['duration_secs']}s"
    for host_id, host_samples in samples.items():
        if condition_held(host_samples, rule["op"], rule["threshold"], duration, now):
            await _open_incident(pool, redis, rule["id"], host_id, message)


async def _open_host_down_incidents(
    pool: asyncpg.Pool, redis: Redis, rule: Any, settings: Settings, hosts_up: set[str]
) -> None:
    # A missing key alone isn't enough: after a Redis outage or flush, keys are missing for
    # hosts that are still reporting. Their last_seen in Postgres is recent, so they don't match.
    stale_hosts = await pool.fetch(
        "SELECT id FROM hosts WHERE last_seen < now() - make_interval(secs => $1)",
        settings.host_ttl_secs,
    )
    message = f"no report for {settings.host_ttl_secs}s"
    for row in stale_hosts:
        if row["id"] not in hosts_up:
            await _open_incident(pool, redis, rule["id"], row["id"], message)


async def evaluate_once(
    pool: asyncpg.Pool, redis: Redis, settings: Settings, check_host_down: bool = True
) -> None:
    now = datetime.now(UTC)
    host_ids = [row["id"] for row in await pool.fetch("SELECT id FROM hosts")]
    # Raises if Redis is down, before anything changes: without Redis, cooldowns and host
    # status are unknown. (With no hosts there's no call to fail, and nothing to change.)
    hosts_up = await _hosts_with_status_key(redis, host_ids)

    # Resolve first: the cooldown it sets then applies to the opening step below.
    await _resolve_recovered(pool, redis, hosts_up)

    rules = await pool.fetch("SELECT * FROM alert_rules WHERE enabled")
    for rule in rules:
        if rule["kind"] == "threshold":
            await _open_threshold_incidents(pool, redis, rule, now)
        elif check_host_down:
            await _open_host_down_incidents(pool, redis, rule, settings, hosts_up)


async def run_engine(pool: asyncpg.Pool, redis: Redis, settings: Settings) -> None:
    started = time.monotonic()
    while True:
        # After an outage of the API or Postgres, hosts look stale only because their reports
        # couldn't be stored. Give them one TTL to report again before treating a missing key
        # as a host going down.
        check_host_down = time.monotonic() - started >= settings.host_ttl_secs
        try:
            await evaluate_once(pool, redis, settings, check_host_down)
        except Exception:  # one bad tick must not stop alerting; the next tick retries
            log.exception("alert evaluation failed")
            started = time.monotonic()
        await asyncio.sleep(settings.alert_interval_secs)
