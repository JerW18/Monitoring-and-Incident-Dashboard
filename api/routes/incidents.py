from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from api.incidents import Status, acknowledge, resolve

router = APIRouter()

MAX_INCIDENTS = 200
# incidents.id is a BIGINT: anything larger is a bad request, not a server error.
IncidentId = Annotated[int, Path(ge=1, le=2**63 - 1)]


@router.get("/v1/incidents")
async def list_incidents(
    request: Request, status: Status | None = None
) -> dict[str, list[dict[str, Any]]]:
    rows = await request.app.state.pool.fetch(
        """
        SELECT i.*, r.name AS rule_name
        FROM incidents i JOIN alert_rules r ON r.id = i.rule_id
        WHERE $1::text IS NULL OR i.status = $1
        ORDER BY i.opened_at DESC, i.id DESC
        LIMIT $2
        """,
        status,
        MAX_INCIDENTS,
    )
    return {"incidents": [dict(row) for row in rows]}


@router.post("/v1/incidents/{incident_id}/ack")
async def ack_incident(request: Request, incident_id: IncidentId) -> dict[str, Any]:
    state = request.app.state
    return dict(await acknowledge(state.pool, state.redis, incident_id))


@router.post("/v1/incidents/{incident_id}/resolve")
async def resolve_incident(request: Request, incident_id: IncidentId) -> dict[str, Any]:
    state = request.app.state
    return dict(await resolve(state.pool, state.redis, incident_id))
