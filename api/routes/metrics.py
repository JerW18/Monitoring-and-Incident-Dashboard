import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from redis.exceptions import RedisError

from api.auth import require_api_key
from api.events import EVENTS_CHANNEL

log = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY_BYTES = 1_000_000
MAX_SAMPLES = 1000
MAX_TS_SKEW = timedelta(minutes=5)
MAX_HISTORY = timedelta(hours=1)
MAX_HISTORY_ROWS = 2000

HOST_ID_PATTERN = r"^[a-z0-9-]{1,64}$"
METRIC_NAME_PATTERN = r"^[a-z_]{1,32}$"

HostId = Annotated[str, StringConstraints(pattern=HOST_ID_PATTERN)]
MetricName = Annotated[str, StringConstraints(pattern=METRIC_NAME_PATTERN)]


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: HostId
    name: MetricName
    # strict: "97" and true are rejected rather than coerced to numbers.
    value: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    ts: AwareDatetime | None = None

    @field_validator("ts")
    @classmethod
    def ts_within_skew(cls, ts: datetime | None) -> datetime | None:
        if ts is not None and abs(ts - datetime.now(UTC)) > MAX_TS_SKEW:
            raise ValueError("ts must be within 5 minutes of the server clock")
        return ts


class MetricsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: Annotated[list[Sample], Field(min_length=1, max_length=MAX_SAMPLES)]


async def read_limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="body too large")
    return bytes(body)


def latest_values(rows: list[tuple[str, str, float, datetime]]) -> dict[str, dict[str, float]]:
    """Newest value of each metric, per host. rows are (host_id, name, value, ts)."""
    latest: dict[str, dict[str, float]] = {}
    for host_id, name, value, _ in sorted(rows, key=lambda row: row[3]):
        latest.setdefault(host_id, {})[name] = value
    return latest


# The body is read by hand, not through a FastAPI body parameter, because FastAPI parses
# the JSON before running dependencies. This way auth runs first and the size is capped.
@router.post(
    "/v1/metrics",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "example": {"samples": [{"host_id": "host-1", "name": "cpu", "value": 42.5}]}
                }
            },
        }
    },
)
async def ingest_metrics(request: Request) -> dict[str, int]:
    body = await read_limited_body(request)
    try:
        batch = MetricsBatch.model_validate_json(body)
    except ValidationError as e:
        raise RequestValidationError(
            e.errors(include_url=False, include_context=False, include_input=False)
        ) from e

    now = datetime.now(UTC)
    rows = [(s.host_id, s.name, s.value, s.ts or now) for s in batch.samples]
    host_ids = list({row[0] for row in rows})

    async with request.app.state.pool.acquire() as conn, conn.transaction():
        # ORDER BY: concurrent batches lock the same hosts in the same order, so can't deadlock.
        await conn.execute(
            """
            INSERT INTO hosts (id)
            SELECT id FROM unnest($1::text[]) AS id ORDER BY id
            ON CONFLICT (id) DO UPDATE SET last_seen = now()
            """,
            host_ids,
        )
        await conn.execute(
            """
            INSERT INTO metrics (host_id, name, value, ts)
            SELECT * FROM unnest($1::text[], $2::text[], $3::float8[], $4::timestamptz[])
            """,
            *zip(*rows, strict=True),
        )

    await update_redis(request, rows)
    return {"accepted": len(rows)}


async def update_redis(request: Request, rows: list[tuple[str, str, float, datetime]]) -> None:
    """Refresh host status and publish. The metrics are already committed at this point,
    and Redis is disposable, so a failure here is logged rather than returned to the agent."""
    ttl = request.app.state.settings.host_ttl_secs
    event = {
        "type": "metrics",
        "samples": [
            {"host_id": host_id, "name": name, "value": value, "ts": ts.isoformat()}
            for host_id, name, value, ts in rows
        ],
    }
    pipe = request.app.state.redis.pipeline(transaction=True)
    for host_id, values in latest_values(rows).items():
        pipe.hset(f"host:{host_id}", mapping=values)
        pipe.expire(f"host:{host_id}", ttl)
    pipe.publish(EVENTS_CHANNEL, json.dumps(event))
    try:
        await pipe.execute()
    except RedisError:
        log.exception("redis update failed after metrics were committed")


@router.get("/v1/metrics")
async def metric_history(
    request: Request,
    host_id: Annotated[str, Query(pattern=HOST_ID_PATTERN)],
    name: Annotated[str, Query(pattern=METRIC_NAME_PATTERN)],
    since: AwareDatetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    earliest = datetime.now(UTC) - MAX_HISTORY
    rows = await request.app.state.pool.fetch(
        """
        SELECT ts, value FROM metrics
        WHERE host_id = $1 AND name = $2 AND ts >= $3
        ORDER BY ts DESC LIMIT $4
        """,
        host_id,
        name,
        max(since, earliest) if since else earliest,
        MAX_HISTORY_ROWS,
    )
    return {"samples": [{"ts": r["ts"], "value": r["value"]} for r in reversed(rows)]}
