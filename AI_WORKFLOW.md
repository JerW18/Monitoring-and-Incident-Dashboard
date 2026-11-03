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
| 4. Simulator | 2026-11-02 21:33:19 | 2026-11-02 21:43:32 | `python -m agent` with fake hosts and `--spike-cpu` and `--stop` fault flags, and a compose service. Before this phase I ran `docker compose down -v` myself to clear the Phase 3 test hosts. | All gates pass with 71 tests. Deliberate code breaks exposed one weak test, which Claude fixed and then re-checked. On compose, 5 hosts report, host-3 stays at 95–99% CPU, host-2 goes down after it stops, and reporting recovers by itself after an api restart. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |
| 5. Alert engine | 2026-11-02 21:53:20 | 2026-11-02 22:17:01 | Background engine, state machine, cooldowns, auto-resolve and the host-down rule. I approved six corrections Claude proposed to its own spec (see Decisions). | All gates pass with 128 tests, including the required idempotency, flapping, host-down and state machine tests. On compose, a spike opened an incident at about 33s and a stopped host at about 53s, both auto-resolved on recovery, and an api outage caused no false alarms. A Redis retry change Claude made turned out to make things worse, so it was measured and reverted. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |
| 6. Incident API and rules CRUD | 2026-11-03 09:52:06 | 2026-11-03 10:03:50 | Expose the state machine over HTTP and add rules CRUD. I approved five decisions Claude proposed (see Decisions). | All gates pass with 198 tests. A new test caught a real 500 on non-finite input, now fixed. All 20 deliberate breaks were caught. On compose, every endpoint and error code behaved as intended, a rule created over the API fired on all 5 hosts, and a manual resolve during a spike held through the 60s cooldown before a new incident opened. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |

The session was paused between 2026-11-01 20:28:38 and 2026-11-02 21:07:19, before Phase 2 started, and between 2026-11-02 22:17:01 and 2026-11-03 09:52:06, before Phase 6 started.

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
| 9 | 4 | `test_keeps_reporting_after_failures` only counted calls. With `raise_for_status` removed, a rejected request (such as a wrong API key) would have counted as success with nothing logged, and the test still passed. | Deliberate code break (mutation check) reported `MISSED` | The test now also checks the logged failures and the recovery message. The same break is now caught. |
| 10 | 4 | A line in `agent/simulator.py` was too long. | `ruff format --check` and `ruff check` (E501) | Shortened the message. |
| 11 | 4 | Claude predicted that without `init: true` the agent would hang for 10s on stop. The behavior was right but the number was wrong. | Measured: without init, `docker stop` took 3.6s and exited 137 (killed) | The log records the measured values. `init: true` stays. |
| 12 | 5 | The spec's "held for the full duration" check could fire on stale data (a host that stopped 25s ago), across a gap in the data, or early for a new host. | Claude's review of the spec while planning the phase, before any code | `condition_held` now needs the last sample at or before the window start and everything after it to breach, with no gap over 10s, up to now. |
| 13 | 5 | Other spec gaps: manual resolve would reopen on the next tick while the condition still held, `cooldown_secs = 0` would send Redis an invalid `EX 0`, and disabled rules' incidents would never clear. | Claude's review of the spec while planning the phase | Resolve always sets the cooldown just before the status write, 0 skips the cooldown, and auto-resolve ignores `enabled`. Each has a test. |
| 14 | 5 | After an API outage every host's key has expired, so the first tick would open false host-down incidents for all of them. | Claude's review while writing the engine | The engine skips the host-down check for one TTL after starting. Confirmed by a test and on compose (a 40s api outage opened nothing, see Verification). |
| 15 | 5 | `test_gap_at_exactly_the_limit_still_fires` had wrong arithmetic: its "10s" gap was really 12s. The over-the-limit test had the wrong gap too, though it passed. | `pytest` failure | Added a `with_gap` helper that states the gap directly. |
| 16 | 5 | One line wasn't formatted as ruff requires, and a test polling loop tripped ruff's ASYNC110. | `ruff format`, `ruff check` | Formatted the line. The loop has to poll Postgres, so it has an explained `noqa`. |
| 17 | 5 | `redis.ping()` at the start of each tick was redundant, because the status pipeline already raises when Redis is down. | Claude's own review | Removed it. |
| 18 | 5 | Claude's first mutation run after these fixes silently repeated Phase 4's list, because the write of the new list had been rejected. All results said `CAUGHT`, but none tested the engine. | Reading the output: the labels were about the simulator | Rewrote the list and ran it again. |
| 19 | 5 | Two tests were too weak: the future-sample test passed even without the future filter (the gap check also rejected its data), and the zero-cooldown test passed without the guard (the Redis error was caught and only logged). | Deliberate code breaks (mutation check) reported `MISSED` for 2 of 20 | The first now uses data where only the filter changes the result, and the second also checks the log. All 20 are caught. |
| 20 | 5 | **A wrong fix by Claude.** A test took 8.2s with Redis down, and Claude concluded the client's default retries were the cause, then added `Retry(NoBackoff(), 1)` with a comment saying so. The test still took 8s. Measured against a frozen Redis, the default gave up in 2.0s and the "fix" took 4.0s, so the change made it twice as slow and the comment was false. | Measuring before accepting the code comment as true (`docker compose pause redis`) | Reverted to the default retries and kept the 2s timeouts. A frozen Redis now costs 2.0s per request. The slow test is now 4s, most likely Windows being slow to refuse connections on localhost; I didn't confirm that. |
| 21 | 6 | `"threshold": 1e999` on the rules endpoints returned a 500 instead of a 422. Validation rejected it correctly, but FastAPI's default error response echoes the rejected value, and `inf` can't be written as JSON, so the error handler itself crashed. | `pytest`: `test_non_finite_threshold_is_rejected` failed with `Out of range float values are not JSON compliant` | Added one validation handler for the whole app that never echoes input. |
| 22 | 6 | A line in `tests/test_incidents_api.py` was too long. | `ruff format --check` and `ruff check` (E501) | Ran `ruff format`. |

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
- **Healthy simulated CPU is limited to 10–70%.** A random walk with no limit could stay above 90% for 30s and open a real incident on a healthy host, ruining the demo.
- **Fault flags naming an unknown host exit with an error**, because a typo would otherwise inject nothing and look like an alerting bug.
- **`init: true` on the agent container:** Python as PID 1 ignores SIGTERM unless it installs a handler. This is one compose line instead of signal-handling code.
- **The agent doesn't resend failed reports.** The next tick sends fresh data, so an api outage leaves a gap in the metrics rather than duplicates.
- **Incident status lives in Postgres, and everything in Redis is best effort after that.** A person can resolve an incident during a Redis outage, and failed cooldowns or events are logged. The engine is the exception: it stops the tick if Redis is down, because it can't read cooldowns or host status.
- **Compare-and-swap with re-check for status changes.** If the status changed underneath an update, the transition is checked again, so acknowledge racing resolve ends resolved instead of returning a false error.
- **The engine's sample query uses `CROSS JOIN LATERAL` per host**, so it uses the `(host_id, name, ts)` index however large `metrics` grows. `EXPLAIN` confirmed the index scan.
- **Known side effect, accepted:** `ON CONFLICT DO NOTHING` uses up a sequence value on each conflict, so incident ids have gaps (the demo's two incidents were 1 and 5). They are only identifiers, so I left this.
- **Rule limits are on purpose:** `duration_secs` is at most 3600 because the engine loads that whole window of samples every tick, and ids are range-checked so an id too large for the column is a 422 rather than a 500.
- **The built-in host-down rule can be renamed, tuned or disabled, but not turned into another kind of rule or deleted.** Only threshold rules can be created.
- **Rule conflicts come from database constraints, not a check beforehand**, so two simultaneous requests can't both pass a check and then collide.
- **The incident list returns the newest 200, with no pagination.** The table grows forever and pagination is out of scope. It's listed as a known limitation.
- **Validation errors never echo the rejected input.** This fixed problem #21, and it also keeps request data out of error responses.
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

**Phase 4** (same environment):
- `ruff format --check .` and `ruff check .`: failed once (problem #10), then passed. `mypy`: no issues in 20 source files.
- `pytest -q`: 71 passed in about 27s.
- Deliberate breaks, each made temporarily and then restored. All 6 are caught now, one only after fixing a weak test (problem #9):
  - host names not checked against fault flags
  - walk not limited to its range
  - `--spike-cpu` having no effect
  - `--stop` having no effect
  - a failed request crashing the loop
  - no `raise_for_status`
- Real CLI: `python -m agent --spike-cpu host-9` exits 2 with `unknown hosts ['host-9']`. With no key, it exits 2 with `an API key is required`.
- `docker compose up --build -d`: all 5 hosts `up`, with row counts growing from 129 to 165 in about 4s.
- `docker compose stop agent`: 0.84s, exit 143. The same image without `init` took 3.6s and exited 137.
- Fault injection with `docker compose run --rm agent --hosts 5 --spike-cpu host-3 --stop host-2`:
  - host-3's CPU stayed at 95.0–99.0.
  - The agent logged `host-2: stopped reporting` at 20s, and host-2's last CPU sample was at 13:42:23 UTC.
  - At 13:43:01 UTC, 38s later, `/v1/hosts` showed host-2 `down` and the other hosts `up`. I checked at that point, so this doesn't measure the exact expiry moment.
- api restart while the agent ran: 22 `report failed` warnings with no agent crash. All 5 hosts logged `reporting again` within 1s of each other, and all 5 were `up` afterwards.

**Phase 5** (same environment):
- `ruff format --check .`, `ruff check .`, `mypy`: failed during the phase (problem #16), now all pass, with no issues in 28 source files.
- `pytest -q`: 128 passed in about 45s against real Postgres and Redis. This includes the required tests:
  - `test_many_concurrent_evaluators_open_exactly_one_incident` (50 concurrent evaluators, 1 incident, 1 event)
  - `test_oscillating_metric_opens_nothing_across_many_ticks` and `test_recovery_then_new_breach_is_blocked_only_by_the_cooldown`
  - `test_host_down_after_ttl_expires_and_resolves_on_next_report`
  - `test_invalid_transitions_are_rejected`, covering every invalid pair
- Deliberate breaks: 20 made one at a time and restored. 18 were caught at first, and all 20 after fixing two tests (problem #19).
- Fault injection on compose with `--spike-cpu host-3 --stop host-2`:
  - 40s healthy run first: 0 incidents.
  - host-3 `High CPU` incident opened 33s after the fault started (`cpu > 90 for 30s`).
  - host-2 `Host down` incident opened 53s after the fault started (20s stop delay + 30s TTL + tick).
  - No incidents on the healthy hosts.
- Normal agent restored: both incidents auto-resolved within 7s. Cooldown keys had TTLs of 57 and 56. 25s later there were still 2 incidents in total and 0 active.
- api stopped for 40s (0 host keys left in Redis), then restarted: incidents stayed at 2 after waiting past the 30s startup grace, and all hosts came back `up`.
- Redis failure timings after the revert (problem #20):
  - Frozen with `docker compose pause`: `POST /v1/metrics` 202 in 2.05s, `GET /v1/hosts` 503 in 2.01s.
  - Stopped: `POST /v1/metrics` 202 in 1.24s, `GET /v1/hosts` 503 in 0.03s.
  - An engine failure was logged during the freeze, with 0 active incidents afterwards.
- `EXPLAIN (ANALYZE)` on the engine's sample query with 5530 rows: `Bitmap Index Scan on metrics_host_name_ts`, 0.15ms.
- Not rerun: the fault-injection demo ran before the retry revert. That revert changes only the Redis client's retry setting, and I reran the tests and failure timings afterwards, but not the full demo.

**Phase 6** (same environment):
- `ruff format --check .` and `ruff check .`: failed once (problem #22), then passed. `mypy`: no issues in 33 source files.
- `pytest -q`: 198 passed in about 57s. It failed once before the fix for problem #21.
- Deliberate breaks: 20 made one at a time and restored, all caught on the first run. They cover the status code mappings, the status filter, ordering and the limit, id range checks, strict input, the duration limits, the built-in rule protections and the history conflict.
- curl on compose:
  - Rules: create 201; duplicate name 409; `1e999` 422; duration 3601 422; `PUT` host-down with a metric 422; delete built-in 409; delete a rule with history 409; unknown rule 404; id too large 422.
  - Incidents: ack 200; second ack 409; ack or resolve on a resolved incident 409; unknown id 404; id too large 422; unknown status 422.
- A rule created over the API (`mem > 1 for 10s`) opened incidents on all 5 running hosts 12s later. After disabling it and resolving them by hand, with a cooldown of 0 and the condition still true, nothing reopened in the next 15s.
- Resolving during a real spike (`--spike-cpu host-3`):
  - The incident opened 36s after the spike started and stayed the only active one for host-3 after an ack.
  - After a manual resolve with CPU still high, nothing reopened 15s later, inside the cooldown.
  - A new incident opened 61s after the resolve (60s cooldown, problem still there), which is the intended behavior.
- Normal agent restored: no active incidents 6s later. `tracebacks in api log: 0` for the whole run.

## Reviewer notes from the human

