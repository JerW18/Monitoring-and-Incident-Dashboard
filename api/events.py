import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

# A pub/sub channel, not a key: it stores nothing, so it has no TTL.
EVENTS_CHANNEL = "events"


async def publish_event(redis: Redis, event: dict[str, Any]) -> None:
    """Best effort: live updates are disposable, and Postgres already has the change."""
    try:
        await redis.publish(EVENTS_CHANNEL, json.dumps(event))
    except RedisError:
        log.exception("failed to publish event")
