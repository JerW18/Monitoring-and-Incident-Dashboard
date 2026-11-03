from typing import Annotated, Any, Literal

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from api.errors import RuleConflict, RuleNotFound
from api.routes.metrics import METRIC_NAME_PATTERN

router = APIRouter()

# alert_rules.id is an INT: anything larger is a bad request, not a server error.
RuleId = Annotated[int, Path(ge=1, le=2**31 - 1)]


class RuleIn(BaseModel):
    # strict: "90" and true are rejected rather than coerced to numbers.
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    metric: Annotated[str, StringConstraints(pattern=METRIC_NAME_PATTERN)] | None = None
    op: Literal[">", "<"] | None = None
    threshold: Annotated[float, Field(allow_inf_nan=False)] | None = None
    # The engine loads this whole window of samples every tick, so it has to be bounded.
    duration_secs: Annotated[int, Field(ge=1, le=3600)] | None = None
    cooldown_secs: Annotated[int, Field(ge=0, le=86400)] = 60
    enabled: bool = True


def check_fields_match_kind(rule: RuleIn, kind: str) -> None:
    condition = (rule.metric, rule.op, rule.threshold, rule.duration_secs)
    if kind == "threshold" and None in condition:
        detail = "a threshold rule needs metric, op, threshold and duration_secs"
    elif kind == "host_down" and any(field is not None for field in condition):
        detail = "the host-down rule only accepts name, cooldown_secs and enabled"
    else:
        return
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def duplicate_name(rule: RuleIn) -> RuleConflict:
    return RuleConflict(f"a rule named {rule.name!r} already exists")


@router.get("/v1/rules")
async def list_rules(request: Request) -> dict[str, list[dict[str, Any]]]:
    rows = await request.app.state.pool.fetch("SELECT * FROM alert_rules ORDER BY id")
    return {"rules": [dict(row) for row in rows]}


@router.post("/v1/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(request: Request, rule: RuleIn) -> dict[str, Any]:
    # Only threshold rules can be created: host_down is built in, and limited to one.
    check_fields_match_kind(rule, "threshold")
    try:
        row = await request.app.state.pool.fetchrow(
            """
            INSERT INTO alert_rules
                (name, kind, metric, op, threshold, duration_secs, cooldown_secs, enabled)
            VALUES ($1, 'threshold', $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            rule.name,
            rule.metric,
            rule.op,
            rule.threshold,
            rule.duration_secs,
            rule.cooldown_secs,
            rule.enabled,
        )
    except asyncpg.UniqueViolationError as e:
        raise duplicate_name(rule) from e
    return dict(row)


@router.put("/v1/rules/{rule_id}")
async def update_rule(request: Request, rule_id: RuleId, rule: RuleIn) -> dict[str, Any]:
    pool = request.app.state.pool
    kind = await pool.fetchval("SELECT kind FROM alert_rules WHERE id = $1", rule_id)
    if kind is None:
        raise RuleNotFound(rule_id)
    check_fields_match_kind(rule, kind)
    try:
        row = await pool.fetchrow(
            """
            UPDATE alert_rules SET
                name = $2, metric = $3, op = $4, threshold = $5,
                duration_secs = $6, cooldown_secs = $7, enabled = $8
            WHERE id = $1
            RETURNING *
            """,
            rule_id,
            rule.name,
            rule.metric,
            rule.op,
            rule.threshold,
            rule.duration_secs,
            rule.cooldown_secs,
            rule.enabled,
        )
    except asyncpg.UniqueViolationError as e:
        raise duplicate_name(rule) from e
    if row is None:  # deleted between the two statements
        raise RuleNotFound(rule_id)
    return dict(row)


@router.delete("/v1/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(request: Request, rule_id: RuleId) -> None:
    pool = request.app.state.pool
    builtin = await pool.fetchval("SELECT builtin FROM alert_rules WHERE id = $1", rule_id)
    if builtin is None:
        raise RuleNotFound(rule_id)
    if builtin:
        raise RuleConflict("a built-in rule can't be deleted; disable it instead")
    try:
        await pool.execute("DELETE FROM alert_rules WHERE id = $1", rule_id)
    except asyncpg.ForeignKeyViolationError as e:
        raise RuleConflict("this rule has incident history; disable it instead") from e
