import asyncio
import logging
from collections.abc import Awaitable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter()

# Upper bound per dependency, so /healthz answers even when a dependency hangs.
CHECK_TIMEOUT_SECS = 2.0


async def _check(name: str, probe: Awaitable[object]) -> bool:
    try:
        await asyncio.wait_for(probe, CHECK_TIMEOUT_SECS)
        return True
    except Exception as e:  # any failure means "unhealthy"; logged, not swallowed
        log.warning("health check %s failed: %r", name, e)
        return False


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    state = request.app.state
    db_ok = await _check("db", state.pool.fetchval("SELECT 1"))
    redis_ok = await _check("redis", state.redis.ping())
    body = {"db": "ok" if db_ok else "error", "redis": "ok" if redis_ok else "error"}
    return JSONResponse(body, status_code=200 if db_ok and redis_ok else 503)
