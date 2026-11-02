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
| 2. Schema and migrations | 2026-11-02 21:07:19 | 2026-11-02 21:16:27 | Alembic setup, `0001_init` in raw SQL, a one-shot `migrate` compose service, schema tests. I also approved Claude's proposal to change test isolation to per-test template database copies. | ruff format, ruff check, mypy and pytest (11 tests) pass locally after fixing two lint/type errors. `migrate` runs before the api and changes nothing on rerun. A deliberately failing migration kept the api from starting and rolled back. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |
| 3. Ingestion API | 2026-11-02 21:21:13 | 2026-11-02 21:29:15 | `POST /v1/metrics` with key auth and validation, plus `GET /v1/hosts` and `GET /v1/metrics`. I approved four changes Claude proposed to the spec (see Decisions). | All gates pass with 56 tests. Five deliberate code breaks were each caught by a test. On the compose stack, curl returned 202, 401, 422 and 413, rows reached Postgres, and hosts went down after the TTL. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |

The session was paused between 2026-11-01 20:28:38 and 2026-11-02 21:07:19, before Phase 2 started.

## Problems found in AI output

| # | Phase | What was wrong | How it was detected | Fix |
|---|---|---|---|---|
| 1 | 2 | A parametrized schema test built its SQL with an f-string, which breaks the "never build SQL by string concatenation" rule in `CLAUDE.md`. The test also had an unnecessary `DELETE`. | Claude's own review, before the first run | Pass whole SQL statements as test parameters and remove the `DELETE`. |
| 2 | 2 | A line in `tests/test_schema.py` was 101 characters, over the 100 limit. | `ruff check` (E501) | Shortened the statement. It still fails the same constraint. |
| 3 | 2 | `admin_execute` in `tests/conftest.py` was typed `sql.Composable`, which psycopg's `execute` doesn't accept. All tests passed at runtime, so only the type checker caught it. | `mypy` (call-overload) | Changed the type to `sql.Composed`. |
| 4 | 3 | The spec's Redis `SET` of a JSON status would have erased a host's other metrics whenever a batch carried only some of them. | Claude's review of the spec while planning the phase, before any code | Use `HSET` + `EXPIRE`. `test_partial_batch_keeps_other_metrics` covers it. |
| 5 | 3 | The spec's host-down rule would have fired false alarms after a Redis outage, because keys are missing for hosts that are still reporting. | Claude's review of the spec while planning the phase | Host down now also requires `hosts.last_seen` older than the TTL. This is written into the spec, to be implemented and tested in Phase 5. |
| 6 | 3 | A line in `tests/test_ingest.py` wasn't formatted as ruff requires. | `ruff format --check` | Ran `ruff format`. |
| 7 | 3 | Hosts were sorted twice, in Python and in SQL. | Claude's own review after the first test run | Kept only the SQL `ORDER BY`. |
| 8 | 3 | Claude's first curl run against compose returned `HTTP 000` for 7 requests. `docker compose up --wait` returned before uvicorn was listening, because the api has no healthcheck. The endpoints weren't at fault, but that evidence was invalid. | Runtime behavior, visible in the command output | Reran the requests once the api was listening. The missing healthcheck isn't fixed yet; it's on the list for Phase 8, where the web service will depend on the api. |

## Decisions and trade-offs

- **SSE instead of WebSocket:** traffic only goes server→client, SSE has built-in browser reconnect, and it passes through an nginx proxy easily. Ack and resolve are plain POST requests.
- **Where state lives:** Postgres holds hosts, metrics, rules and incidents. Redis holds only `host:{id}` (TTL 30s, expiry means the host is down), `cooldown:{rule}:{host}` (TTL = rule cooldown) and the pub/sub channel.
- **One active incident per host+rule:** the partial unique index covers both `open` and `acknowledged`, because an acknowledged incident is still active.
- **asyncpg with raw SQL, no ORM:** the important queries (`UNNEST` batch insert, `ON CONFLICT DO NOTHING`, compare-and-swap status update) stay visible. Alembic is used only for migrations.
- **One Redis pubsub listener fanning out to per-client bounded queues**, instead of one Redis connection per SSE client. A slow client drops events instead of blocking the others.
- **ruff's `ASYNC` rules are enabled** to flag blocking calls (such as `time.sleep` or sync HTTP) inside async functions, which is one of the code standards in `CLAUDE.md`.
- **Tests use Redis DB 1** (the app uses DB 0), so running the tests never wipes live dashboard state.
- **`/healthz` uses a 2s timeout per dependency**, so it returns 503 instead of hanging when Postgres or Redis is unreachable.
- **Test isolation uses per-test database copies:** migrations run once into a template database, and each test gets `CREATE DATABASE ... TEMPLATE`. This replaced the original truncate-and-re-seed plan, so tests start from what the real migration produces and there's no second copy of the seed data. The full suite of 11 tests takes about 5.5s, 2s of which is the Redis-unreachable health test.
- **Migrations run in a one-shot `migrate` service**, and the api waits for it to exit successfully. A failed migration stops the api from starting instead of letting it run against the wrong schema.
- **`psycopg` is installed only for Alembic and test setup.** The app itself uses asyncpg, so request paths never touch a sync driver.
- **Host status is a Redis hash (`HSET` + `EXPIRE` in one atomic pipeline)**, not a JSON string, so a partial batch merges instead of overwriting. Metric names can't collide with other fields because the hash holds only metrics.
- **The ingest body is read by hand after the auth check.** FastAPI parses JSON before running dependencies, which would give unauthenticated callers a 422 and an unlimited read. Now they get a 401, and bodies over 1 MB get a 413. The cost is that the request body in the API docs is declared by hand, as an example only.
- **If Redis fails after the Postgres commit, ingest logs the error and returns 202.** The metrics are stored and Redis is disposable, so an error response would wrongly imply data loss.
- **`GET /v1/hosts` returns 503 when Redis is unreachable**, rather than reporting every host as down.
- **`value` is strictly numeric:** `"97"` and `true` are rejected instead of coerced.
- **Hosts are upserted in sorted order**, so concurrent batches can't deadlock. Removing the sort made the concurrency test fail, so the risk is real.
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

**Phase 2** (same environment):
- `ruff format --check .`: exit 0. `ruff check .`: failed once (problem #2), then passed. `mypy`: failed once (problem #3), then no issues in 11 source files.
- `pytest -q`: 11 passed. `pg_database` showed 0 leftover `monitor_test%` databases afterwards.
- `test_migration_downgrades_and_upgrades_again` runs a real downgrade to empty and upgrades again.
- `docker compose up --build -d --wait`: `migrate` logged `Running upgrade -> 0001` and exited 0, then the api started and `/healthz` returned 200.
- `psql \d incidents` showed `incidents_one_active UNIQUE ... WHERE status = ANY (ARRAY['open', 'acknowledged'])`, and `alert_rules` held the two seeded rules.
- Rerunning `docker compose up migrate`: no `Running upgrade` line, exit 0.
- Failure test: I mounted a throwaway `0002` migration (creates a table, then `SELECT 1/0`) into `migrate` for one run. Compose reported `service "migrate" didn't complete successfully: exit 1`, the api stayed down (curl couldn't connect), `alembic_version` stayed at `0001`, and the table didn't exist, so the transaction rolled back. The file was never in the repo, and the normal stack came back with `/healthz` 200.

**Phase 3** (same environment):
- `ruff format --check .`: failed once (problem #6), then exit 0. `ruff check .`: passed. `mypy`: no issues in 16 source files.
- `pytest -q`: 56 passed in about 24s against real Postgres and Redis.
- Deliberate breaks, each made temporarily and then restored. All were caught:
  - no sorted upsert: `test_concurrent_overlapping_batches_do_not_deadlock` failed
  - latest value taken by batch position instead of `ts`: `test_redis_status_has_latest_value_and_ttl` failed
  - no `EXPIRE`: 2 tests failed
  - API key check always passing: 4 tests failed
  - Redis failure not caught: `test_redis_down_still_accepts_and_stores` failed
- curl against the compose stack:
  - valid batch: `202 {"accepted":3}`
  - missing key, wrong key, and missing key with malformed JSON: 401
  - bad host id, `1e999` and malformed JSON: 422
  - 1 MB + 1 byte: 413
- `psql` showed the 3 rows. `redis-cli` showed `HGETALL host:web-1` with cpu and mem, and `TTL` 28.
- TTL expiry: 35s after `web-1` last reported, `GET /v1/hosts` showed it `down` with no metrics. I checked once at that point, so this doesn't measure the exact expiry moment.
- Redis stopped: `POST` returned 202 in 1.27s, the row reached Postgres, the api logged `redis update failed after metrics were committed`, and `GET /v1/hosts` returned 503.

## Reviewer notes from the human

