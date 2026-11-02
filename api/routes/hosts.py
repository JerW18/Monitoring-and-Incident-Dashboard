from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from redis.exceptions import RedisError

router = APIRouter()


@router.get("/v1/hosts")
async def list_hosts(request: Request) -> dict[str, list[dict[str, Any]]]:
    rows = await request.app.state.pool.fetch(
        "SELECT id, first_seen, last_seen FROM hosts ORDER BY id"
    )
    pipe = request.app.state.redis.pipeline(transaction=False)
    for row in rows:
        pipe.hgetall(f"host:{row['id']}")
    try:
        statuses = await pipe.execute()
    except RedisError as e:
        # Without Redis we can't tell up from down, so don't report every host as down.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "host status unavailable") from e

    return {
        "hosts": [
            {
                "id": row["id"],
                # An expired key reads as empty: the host stopped reporting.
                "status": "up" if values else "down",
                "metrics": {name: float(value) for name, value in values.items()},
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
            for row, values in zip(rows, statuses, strict=True)
        ]
    }
