# AI Workflow Log

I update this log at the end of every phase. It records only what actually happened in the session.

## Tools and setup

- **Tool:** Claude Code (VS Code extension) on Windows 11
- **Model:** Claude Opus 5.5 (`claude-opus-5-5`)
- **Mode:** Claude writes a short plan per phase and I approve it before code is written. Implementation runs in auto mode, and I review after every phase. Claude doesn't commit; it gives me the files to stage and a commit message, and I make the commit.
- **Project rules:** I wrote `CLAUDE.md` to set the scope, code standards and required tests. The approved plan is `SPEC.md`.
- **Local environment:** Windows 11, Python 3.12.7, Node v24.21.0, Docker 29.9.2.

## Timeline

Timestamps are Hong Kong Time (HKT, UTC+8) and come from `date` (`TZ=UTC-8 date` in Git Bash).

| Phase | Start | End | What I asked | Outcome |
|---|---|---|---|---|
| Spec | 2026-11-01 20:10:17 | 2026-11-01 20:21:51 | Rewrite the spec for a Python backend, update `CLAUDE.md`, delete irrelevant files, start from scratch | `SPEC.md` and `CLAUDE.md` rewritten for Python (FastAPI, asyncpg, Alembic). Old files deleted. I switched dependency management from uv to pip + venv, asked Claude whether Rust or Python is easier for others to run, then approved the spec. |
| 1. Skeleton, compose, CI | 2026-11-01 20:21:51 | 2026-11-01 20:28:38 | Pinned requirements, FastAPI app with `/healthz`, Dockerfile, compose (Postgres, Redis, api), GitHub Actions CI, ruff/mypy/pytest config | ruff format, ruff check, mypy and pytest (2 tests against real Postgres and Redis) pass locally. The compose stack runs on Linux: `/healthz` returns 200, returns 503 with Redis stopped, and the api shuts down cleanly on SIGTERM. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |

## Problems found in AI output

_None yet._

## Decisions and trade-offs

- **SSE instead of WebSocket:** traffic only goes server→client, SSE has built-in browser reconnect, and it passes through an nginx proxy easily. Ack and resolve are plain POST requests.
- **Where state lives:** Postgres holds hosts, metrics, rules and incidents. Redis holds only `host:{id}` (TTL 30s, expiry means the host is down), `cooldown:{rule}:{host}` (TTL = rule cooldown) and the pub/sub channel.
- **One active incident per host+rule:** the partial unique index covers both `open` and `acknowledged`, because an acknowledged incident is still active.
- **asyncpg with raw SQL, no ORM:** the important queries (`UNNEST` batch insert, `ON CONFLICT DO NOTHING`, compare-and-swap status update) stay visible. Alembic is used only for migrations.
- **One Redis pubsub listener fanning out to per-client bounded queues**, instead of one Redis connection per SSE client. A slow client drops events instead of blocking the others.
- **ruff's `ASYNC` rules are enabled** to flag blocking calls (such as `time.sleep` or sync HTTP) inside async functions, which is one of the code standards in `CLAUDE.md`.
- **Tests use Redis DB 1** (the app uses DB 0), so running the tests never wipes live dashboard state.
- **`/healthz` uses a 2s timeout per dependency**, so it returns 503 instead of hanging when Postgres or Redis is unreachable.
- **A single uvicorn worker**, so there is one alert engine per container. DB idempotency keeps duplicate engines safe anyway.

## Verification

**Phase 1** (Windows host with Python 3.12.7 venv, Docker 29.9.2):
- `ruff format --check .`: exit 0. `ruff check .`: all checks passed. `mypy`: no issues in 8 source files.
- `pytest -v`: 2 passed against compose Postgres and Redis:
  - `test_healthz_ok_with_real_postgres_and_redis`
  - `test_healthz_503_when_redis_unreachable` (points Redis at an unused port)
- `docker compose up --build -d --wait`: all three services up.
- `curl -i localhost:8000/healthz`: `200 {"db":"ok","redis":"ok"}`.
- `docker compose stop redis`, then curl: `503 {"db":"ok","redis":"error"}` in 0.09s. The api logged `WARNING api.routes.health health check redis failed: network:ConnectionError`. After restarting Redis it returns 200 again.
- `docker compose stop api`: uvicorn logged `Shutting down`, then `Application shutdown complete` (graceful SIGTERM).
- Phase 1 had no problems: every gate passed on the first run.

## Reviewer notes from the human

