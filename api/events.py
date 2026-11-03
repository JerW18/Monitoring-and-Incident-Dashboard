"""Live updates: publishing to Redis, and passing what arrives on to connected browsers."""

import asyncio
import json
import logging
from typing import Any, NamedTuple

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

MAX_QUEUED_EVENTS = 256
RECONNECT_DELAY_SECS = 1.0
CONNECT_TIMEOUT_SECS = 2.0


def events_channel(redis: Redis) -> str:
    """Redis pub/sub ignores DB numbers, so the channel names it, the way keys are separated.

    A channel isn't a key: it stores nothing, so it has no TTL.
    """
    return f"events:{redis.connection_pool.connection_kwargs.get('db', 0)}"


async def publish_event(redis: Redis, event: dict[str, Any]) -> None:
    """Best effort: live updates are disposable, and Postgres already has the change."""
    try:
        await redis.publish(events_channel(redis), json.dumps(event))
    except RedisError:
        log.exception("failed to publish event")


class Event(NamedTuple):
    type: str
    data: str


# Tells a client that it may have missed events, so it should refetch everything.
RESYNC = Event("resync", "{}")

# Queues hold events, then None to tell the stream to end.
ClientQueue = asyncio.Queue[Event | None]


class EventHub:
    """One Redis subscription, passed on to one bounded queue per connected browser."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.clients: set[ClientQueue] = set()
        self.subscribed_before = False

    def subscribe(self) -> ClientQueue:
        queue: ClientQueue = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)
        queue.put_nowait(RESYNC)  # the subscription is live, so a fetch now can't miss anything
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue: ClientQueue) -> None:
        self.clients.discard(queue)

    def broadcast(self, event: Event) -> None:
        for queue in list(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._disconnect_slow_client(queue)

    def _disconnect_slow_client(self, queue: ClientQueue) -> None:
        # Losing events silently would leave the dashboard wrong with no sign of it. Ending
        # the stream instead makes the browser reconnect, and it refetches on resync.
        log.warning("disconnecting a slow SSE client")
        self.unsubscribe(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)

    def _handle(self, message: dict[str, Any]) -> None:
        if message["type"] == "subscribe":
            # The client also resubscribes by itself after a dropped connection, so every
            # subscribe after the first means events may have been missed.
            if self.subscribed_before:
                log.info("redis subscription restored")
                self.broadcast(RESYNC)
            self.subscribed_before = True
        elif message["type"] == "message":
            self.broadcast(Event(json.loads(message["data"])["type"], message["data"]))

    async def run(self) -> None:
        """Runs until cancelled. A failure must not end live updates until the next restart."""
        while True:
            try:
                # Its own client, so each retry starts clean and closes what it opened.
                client = Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=CONNECT_TIMEOUT_SECS,
                )
                async with client, client.pubsub() as pubsub:
                    await pubsub.subscribe(events_channel(client))
                    async for message in pubsub.listen():
                        self._handle(message)
            except Exception as e:  # any failure means retry; logged, not swallowed
                log.warning("redis subscription lost: %r", e)
            await asyncio.sleep(RECONNECT_DELAY_SECS)
