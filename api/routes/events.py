from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

router = APIRouter()

PING_SECS = 15
# A client that stopped reading must not hold its connection open forever.
SEND_TIMEOUT_SECS = 10


@router.get("/v1/events")
async def events(request: Request) -> EventSourceResponse:
    hub = request.app.state.hub

    async def stream() -> AsyncIterator[ServerSentEvent]:
        # Inside the generator, so that its finally always pairs with this subscribe.
        queue = hub.subscribe()
        try:
            while (event := await queue.get()) is not None:
                yield ServerSentEvent(data=event.data, event=event.type)
        finally:
            hub.unsubscribe(queue)

    return EventSourceResponse(stream(), ping=PING_SECS, send_timeout=SEND_TIMEOUT_SECS)
