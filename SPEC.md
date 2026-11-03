# Spec: IT monitoring & incident dashboard (take-home)

## Context
This is a small, correct and well-verified MVP. A simulator sends host metrics to a Python API. Alert rules turn sustained bad metrics into incidents, which move open → acknowledged → resolved. A React dashboard shows host status, metrics and incidents live. The scope is fixed by `CLAUDE.md`. Reviewers grade how the AI is directed and verified, so `AI_WORKFLOW.md` must stay truthful and current.

## Stack
- **Python 3.12**, plain **pip** + **venv** (`.venv/`). Dependencies pinned with `==` in `requirements.txt` (runtime) and `requirements-dev.txt` (`-r requirements.txt` + ruff, mypy, pytest). `pyproject.toml` holds tool config only (ruff, mypy, pytest).
- **API:** FastAPI + uvicorn. Validation with Pydantic v2.
- **Postgres:** asyncpg, with raw parameterized SQL and no ORM. Migrations are **Alembic** revisions written as plain SQL (`op.execute`), run by a one-shot `migrate` compose service before the API starts.
- **Redis:** `redis.asyncio`
- **Simulator:** Python asyncio + httpx
- **Frontend:** TypeScript + React (Vite) in `web/`
- **Quality gates:** `ruff format --check`, `ruff check`, `mypy` (typed defs required) and `pytest` (with pytest-asyncio), against real Postgres and Redis.
- **Runtime:** Docker Compose. `docker compose up` runs everything on Linux.
- **CI:** GitHub Actions with Postgres and Redis service containers, running the gates above plus `npm ci && npm run build` for `web/`.

## Architecture
```
agent (sim) ──POST /v1/metrics (X-API-Key)──► api (FastAPI, 1 uvicorn worker)
                                               ├─ Postgres: hosts, metrics, rules, incidents (truth)
                                               ├─ Redis: host:{id} (TTL 30s), cooldown:{rule}:{host} (TTL), PUBLISH events
                                               ├─ alert engine (asyncio task started in lifespan, ticks every 5s)
                                               └─ GET /v1/events (SSE) ◄─ one Redis pubsub listener → per-client asyncio.Queue
web (Vite build, nginx) ── /v1/* proxied to api ── EventSource /v1/events
```
- **One Python project at the repo root** with packages `api/` and `agent/`. It builds one Docker image: the `api`, `agent` and `migrate` services use the same image with different commands.
- **asyncpg with raw SQL rather than an ORM.** The queries are few and specific: an `UNNEST` batch insert, `ON CONFLICT DO NOTHING`, and a compare-and-swap update. Plain SQL keeps those visible and reviewable. Alembic (via SQLAlchemy) is used only for migrations, never in request paths.
- **SSE rather than WebSocket.** Traffic only goes server→client, SSE reconnects automatically and passes through nginx easily. Ack and resolve are plain POST requests.
- **Fan-out:** one Redis pubsub listener task pushes to a set of bounded `asyncio.Queue`s, one per SSE client. The handler removes its queue in a `finally` block on disconnect. If a slow client's queue is full, its stream ends rather than blocking the others or silently losing events: the browser reconnects and refetches. The listener reconnects by itself after a Redis failure. Every connection's first event is `resync`, sent again after the subscription recovers, which means "refetch everything". The channel is `events:{redis db}` because pub/sub ignores DB numbers, which keeps tests and a running stack apart.
- **A single uvicorn worker**, so there is exactly one alert engine per container. Running duplicate engines would still be safe because incident creation is idempotent in the DB.

## Schema (Alembic revision `0001_init`, raw SQL)
- `hosts(id TEXT PK, first_seen, last_seen TIMESTAMPTZ)`: upserted on ingest. This is the set of hosts that the host-down rule checks.
- `metrics(id BIGSERIAL, host_id TEXT FK, name TEXT, value DOUBLE PRECISION, ts TIMESTAMPTZ)` with index `(host_id, name, ts DESC)`.
- `alert_rules(id SERIAL, name UNIQUE, kind CHECK IN ('threshold','host_down'), metric, op CHECK IN ('>','<'), threshold, duration_secs, cooldown_secs DEFAULT 60, enabled, builtin, created_at)`. A CHECK requires all threshold fields when `kind='threshold'` and none when `kind='host_down'`. A partial unique index allows only one `host_down` rule. Seeded with the built-in "Host down" rule and "High CPU" (`cpu > 90` for 30s).
- `incidents(id BIGSERIAL, host_id FK, rule_id FK ON DELETE RESTRICT, status CHECK IN ('open','acknowledged','resolved'), message, opened_at, acknowledged_at, resolved_at)`.
  - CHECK constraints: `acknowledged` requires `acknowledged_at`, and `resolved_at` is set exactly when the status is `resolved`.
  - **Partial unique index:** `UNIQUE (host_id, rule_id) WHERE status IN ('open','acknowledged')`. An acknowledged incident is still active. Creation uses `INSERT ... ON CONFLICT DO NOTHING`.
  - Index `(status, opened_at DESC)` for the list endpoint.
  - RESTRICT means a rule with incident history can't be deleted (that returns 409). Disable the rule instead.

## API (`api/`)
| Endpoint | Notes |
|---|---|
| `POST /v1/metrics` | `X-API-Key` compared to env `API_KEY` with `hmac.compare_digest`. Pydantic validation: 1–1000 samples, host_id `^[a-z0-9-]{1,64}$`, name `^[a-z_]{1,32}$`, finite value, optional ts within ±5 min (defaults to now). The key is checked before the body is read, and bodies over 1 MB get 413. One transaction does the hosts upsert (in sorted order, to avoid deadlocks) and an `INSERT ... SELECT FROM UNNEST($1::text[], ...)`. Then an atomic pipeline runs `HSET host:{id} <metric> <latest value> ...`, `EXPIRE host:{id} 30` and `PUBLISH events:{db}`. If Redis fails after the commit, the error is logged and the response is still 202, because the metrics are stored. Returns 202 `{accepted: n}` |
| `GET /v1/hosts` | Hosts from PG plus up/down and latest values from Redis (pipelined `HGETALL`). 503 if Redis is unreachable, because status is unknown |
| `GET /v1/metrics?host_id&name&since` | Chart history. `since` is clamped to the last 1h, and the newest 2000 rows are returned, oldest first |
| `GET /v1/incidents?status=` | Newest 200 incidents, with the rule name |
| `POST /v1/incidents/{id}/ack`, `/resolve` | Go through the state machine. 404 if the incident is unknown, 409 on an invalid transition |
| `GET/POST /v1/rules`, `PUT/DELETE /v1/rules/{id}` | Pydantic-validated: `duration_secs` 1–3600 (the engine loads that window every tick), `cooldown_secs` 0–86400, finite `threshold`. Only threshold rules can be created. `PUT` on the built-in rule accepts only `name`, `cooldown_secs` and `enabled`. Built-in rules can't be deleted. A duplicate name or a rule with history returns 409, from the database constraint |
| `GET /v1/events` | SSE with `resync`, `metrics` and `incident` events, and a keep-alive ping every 15s |
| `GET /healthz` | `SELECT 1` and Redis `PING`, each with a 2s timeout. 200, or 503 naming the failing dependency |

Modules:
- `config.py` (pydantic-settings)
- `db.py` (asyncpg pool)
- `errors.py` (domain exceptions mapped to HTTP status codes by exception handlers)
- `routes/{metrics,hosts,incidents,rules,events}.py`
- `incidents.py` (the state machine)
- `alerts/{evaluate.py, engine.py}`
- `main.py` (app factory and lifespan)

`create_app(settings)` lets the tests drive the app with `httpx.AsyncClient(transport=ASGITransport(app))`.

**State machine (`incidents.py`, the only place transitions are defined):** the allowed transitions are `open→acknowledged`, `open→resolved` and `acknowledged→resolved`. Anything else raises `InvalidTransition`. The DB write is a compare-and-swap, `UPDATE ... SET status=$new WHERE id=$1 AND status=$expected RETURNING ...`. If it returns no row, the status changed underneath it, so the transition is re-read and re-checked: acknowledge racing resolve still ends resolved, and an invalid result is a 409 (or a 404 if the incident doesn't exist). Manual resolve and auto-resolve both use this path, and both set the cooldown just before the write, so clicking Resolve while the condition still holds doesn't reopen the incident on the next tick. Cooldown and event failures are logged, not raised, because Postgres is the source of truth.

## Alert engine
- An asyncio task created in the FastAPI lifespan and cancelled on shutdown. Each tick (5s, configurable) calls `evaluate_once(pool, redis, settings)`. Exceptions are logged and the loop keeps running. If Redis is unreachable the tick stops before changing anything, because cooldowns and host status are unknown.
- **Threshold rules:** fetch each host's samples in `[now - duration - grace, now]`. The pure function `evaluate.condition_held(...)` returns true only when the last sample at or before the window start and every sample after it all breach, and no gap between samples, or between the last sample and now, exceeds `grace` (10s). So one bad sample, a short breach, stale data or a gap can't fire the rule, and neither can a new host with too little history. If the condition holds and no cooldown key exists, the engine runs `INSERT ... ON CONFLICT DO NOTHING` and publishes an event only when a row was inserted.
- **Auto-resolve:** when an active incident exists and the newest sample no longer breaches, it resolves through the state machine and runs `SET cooldown:{rule}:{host} 1 EX cooldown_secs` (skipped when `cooldown_secs` is 0). If a host stops reporting while still breaching, the incident stays open, because recovery is unknown. Disabled rules open nothing new, but their active incidents still auto-resolve.
- **Flapping:** the full-duration requirement means an oscillating metric never fires. The cooldown blocks immediate re-opening after recovery.
- **Host down:** a known host gets a host_down incident when its `host:{id}` key is missing **and** `hosts.last_seen` is older than the TTL. The second check prevents false alarms after a Redis outage or flush, when keys are missing for hosts that are still reporting. The engine also skips this check until it has been running for one TTL: after an API outage, hosts look stale only because they couldn't report. When the key reappears, the incident auto-resolves, with the same cooldown. `HOST_TTL_SECS` (default 30) is configurable so tests can use 2s.
- **Redis keys:** every key has a TTL. The pub/sub channel isn't a key and stores nothing.

## Simulator (`agent/`)
`python -m agent` with flags `--api-url --api-key --hosts N --interval 2 --spike-cpu host-3 (repeatable) --stop host-2 (repeatable) --stop-after 20`. Each host is an asyncio task sending cpu, mem and disk as a random walk. Healthy CPU stays within 10–70%, so a healthy host can't open an incident by chance, and spiked hosts stay within 95–99%. Fault flags naming an unknown host are rejected at startup. The agent sends no timestamps; the server assigns them. It uses one shared `httpx.AsyncClient`, and on errors it logs and retries next tick. Compose runs 5 healthy hosts. To demo a fault: `docker compose stop agent && docker compose run --rm agent --hosts 5 --spike-cpu host-3 --stop host-2`.

## Frontend (`web/`)
Vite, React and TypeScript, with no UI or chart libraries. The chart is a small hand-written SVG line chart. One page:
- a host grid (green/red with latest values)
- a CPU chart for the selected host (history from GET, then appended from SSE)
- an incidents table with Ack and Resolve buttons, refetched on `incident` SSE events.

Served by nginx, which proxies `/v1` to the api with SSE buffering off.

## Tests (pytest against real Postgres and Redis)
- Test isolation: a session fixture runs `alembic upgrade head` into a template database. Each test gets its own fresh copy (`CREATE DATABASE ... TEMPLATE`), so it starts from exactly what the real migration produces, with no second copy of the seed data. Redis uses DB 1 with `FLUSHDB`.
- **Unit:** state machine (every invalid transition rejected, valid ones accepted). `condition_held`: sustained breach fires, while a short breach, an oscillating series and a data gap do not.
- **Integration:**
  1. **Idempotency:** 50 concurrent `evaluate_once` calls via `asyncio.gather` produce exactly 1 open incident.
  2. **Flapping:** breach → incident → recovery → auto-resolve → re-breach within cooldown produces no second incident.
  3. **Host-down:** ingest with a 2s TTL, wait 3s, evaluate, and the host_down incident is open. Ingest again, evaluate, and it is resolved.
  4. **Ingest HTTP:** missing or wrong key gets 401, a bad payload gets 422, and a valid batch gets 202 with rows in PG and a key with a TTL in Redis.
  5. **API state machine:** acking a resolved incident returns 409.
  6. **Schema constraints:** one active incident per host+rule (acknowledged counts as active), threshold rule fields required, only one host_down rule.

## Phases (each one: build → run → test → show output → update AI_WORKFLOW.md → hand the human a commit list → pause for review)
1. Skeleton, compose and CI: `requirements*.txt`, `pyproject.toml` (tool config), the FastAPI app with `/healthz`, the Dockerfile, compose (postgres, redis, api), ci.yml, ruff and mypy config.
2. Schema: Alembic setup, `0001_init`, the `migrate` service and the schema tests.
3. Ingestion API, plus `GET /v1/hosts` and `GET /v1/metrics`.
4. Simulator.
5. Alert engine, the state machine and their tests.
6. Incident API (on the existing state machine) and rules CRUD.
7. SSE.
8. Frontend and the full `docker compose up`.
9. Tests: complete the integration suite.
10. README with Mermaid diagram, curl, fault-injection and test instructions, and known limitations. Compute the total elapsed time in AI_WORKFLOW.

## Known limitations (README)
- No user accounts, and no auth on dashboard endpoints (only ingest requires a key), so anyone who can reach the API can acknowledge incidents or change rules.
- `GET /v1/incidents` returns only the newest 200, with no pagination.
- A Redis subscription connection that dies silently (no TCP close) isn't detected, so live updates would stop until the API restarts. A Redis restart is handled.
- No multi-tenancy, notifications or Kubernetes.
- No metric retention or downsampling.
- A single API instance and worker is assumed, with no engine leader election. DB idempotency keeps duplicate engines safe.
- After a Postgres outage, hosts can look stale for one tick and briefly open false host-down incidents, which then auto-resolve.

## Verification (end-to-end)
1. `docker compose up --build` brings up the dashboard at http://localhost:8080 with 5 green hosts.
2. `docker compose stop agent && docker compose run --rm agent --hosts 5 --spike-cpu host-3 --stop host-2`:
   - after about 30s, a CPU incident opens for host-3
   - about 30s after host-2 stops, its tile turns red and a host_down incident opens
   - both appear live, without a refresh.
3. Ack and then Resolve in the UI. A second ack returns 409.
4. Restart the normal agent, and the host-down incident auto-resolves.
5. `docker compose up -d postgres redis && pytest` passes, and CI is green on push.
