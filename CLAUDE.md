# CLAUDE.md

Project context and working rules for Claude Code. Read this at the start of every session.

## Purpose

Take-home project for a software engineer job application. Reviewers grade how effectively the human directs AI, evaluates its output, and reaches a working result efficiently. Keep scope small. Correctness and verification matter more than size or documentation volume.

## Project: IT monitoring and incident dashboard

Agents (or a simulator) send host metrics to a Python API. Alert rules turn sustained bad metrics into incidents. Incidents move through open -> acknowledged -> resolved. A web dashboard shows live host status, metrics and incidents.

## Stack (fixed, do not change without asking)

- Backend: Python 3.12 (FastAPI, asyncio, asyncpg with raw parameterized SQL, Alembic migrations), plain pip + venv with pinned `requirements.txt` / `requirements-dev.txt`
- Storage: Postgres is the source of truth. Redis holds disposable state only: latest host status with TTL, alert cooldowns, pub/sub for live updates.
- Frontend: TypeScript, React (Vite), in `web/`
- Runtime: Docker Compose. `docker compose up` must run everything on Linux.
- CI: GitHub Actions running `ruff format --check`, `ruff check`, `mypy`, `pytest` (plus `npm run build` for `web/`)

## Layout

- `api/` Python API and alert engine (FastAPI)
- `agent/` Python simulator (fake hosts, fault injection), run as `python -m agent`
- `web/` React + TypeScript dashboard
- `migrations/` Alembic migrations (raw SQL)
- `docker-compose.yml`, `.github/workflows/ci.yml`
- `README.md`, `AI_WORKFLOW.md`, `SPEC.md`

## MVP scope (do not exceed)

1. `POST /v1/metrics`: batch ingest, API-key header auth, validation, batched insert, updates Redis host status with a 30s TTL (expired key = host down).
2. Simulator: spawns N fake hosts, sends metrics every few seconds, flags to inject faults (e.g. `--spike-cpu host-3`, `--stop host-2`).
3. Alert engine: background asyncio task. A rule fires only when its condition holds for the full duration. Incident creation is idempotent (partial unique index on one open incident per host+rule, plus `INSERT ... ON CONFLICT DO NOTHING`). Redis cooldown against flapping. Auto-resolve on recovery. Built-in "host down" rule.
4. Incident API: list, ack, resolve, rules CRUD. State transitions enforced in one place.
5. Live updates: Redis pub/sub -> SSE endpoint -> browser.
6. Dashboard page: host status grid, live metric chart, incidents table with ack/resolve buttons.

Out of scope (list as known limitations in the README): user accounts, multi-tenancy, notifications, Kubernetes.

## How to work

1. For any new phase, propose a short plan first and wait for approval before writing code.
2. Work in small phases in this order: skeleton + compose + CI, schema/migrations, ingestion API, simulator, alert engine, incident API, SSE, frontend, tests, README.
3. After each phase: build it, run it, run its tests, and show the command output as evidence. Do not call a phase done until it actually runs. Then pause for human review before the next phase.
4. One git commit per phase with a clear message. The human makes the commits; Claude lists the files to stage and a suggested message.
5. Prefer simple, readable code. No speculative abstractions.

## Code standards

- No bare `except:` or swallowed exceptions in request paths. Raise domain exceptions and map them to HTTP responses in exception handlers.
- No blocking calls in async code (no `requests`, `time.sleep`, or sync DB drivers in request paths). Use `asyncio.to_thread` if unavoidable.
- Every Redis key gets a TTL unless there is a written reason not to.
- Validate all external input. Never build SQL by string concatenation.
- Index for the queries actually used (e.g. `metrics(host_id, name, ts DESC)`).
- SSE handlers must clean up when the client disconnects.
- Keep the incident state machine in one module. Invalid transitions return an error.

## Required tests

- Alert idempotency: the evaluator run concurrently many times yields exactly one open incident.
- Incident state machine: invalid transitions are rejected.
- Flapping: a metric oscillating around the threshold does not create repeated incidents.
- Host-down detection after the TTL expires.
- At least one integration test against real Postgres and Redis.

## AI_WORKFLOW.md (maintain continuously, this is graded)

Update `AI_WORKFLOW.md` at the end of every phase. It must be truthful: record only what actually happened in the session. Never invent mistakes, timings or results.

Structure:
- **Tools and setup:** Claude Code, model, mode used
- **Timeline table:** phase, start/end timestamps (get them from `date`, never guess), what was asked, outcome
- **Problems found in AI output:** for each, what was wrong, how it was detected (test, clippy, human review, runtime behavior), and the fix. Log a bug immediately when you find one in your own earlier code or when the human points one out.
- **Decisions and trade-offs:** e.g. SSE vs WebSocket, where state lives
- **Verification:** tests run, manual fault-injection demo steps
- **Reviewer notes from the human:** leave empty for the human to fill in. Do not write in this section.

At the end, compute total elapsed time from the timestamps.

## README.md

Brief intro, a Mermaid architecture diagram, `docker compose up` instructions, example curl commands, how to run the simulator and inject a fault, how to run tests, and known limitations.

## Do not

- Expand scope beyond the MVP list without asking.
- Commit secrets. Use `.env.example` and compose environment defaults for local dev only.
- Claim something works without running it.
- Edit `AI_WORKFLOW.md` to make the process look better than it was.
