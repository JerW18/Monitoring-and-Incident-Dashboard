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
| 7. SSE | 2026-11-03 10:07:25 | 2026-11-03 10:29:43 | Redis pub/sub to an SSE endpoint, with cleanup on disconnect. I approved five decisions Claude proposed. I also noticed the demo was taking too long and asked if it was done, which is when Claude found the run was invalid. | All gates pass with 212 tests, with the real agent publishing. This fixed a required test that had been failing whenever the stack was running. On compose, metrics and incident events streamed live, the stream survived a Redis restart, and `stop api` took about 1.6s with a stream open. One shutdown error line remains (problem #28). The CI workflow isn't verified on GitHub yet because nothing has been pushed. |
| 8. Frontend and full compose | 2026-11-03 10:35:28 | 2026-11-03 10:53:57 | React dashboard, nginx, an api healthcheck and the CI web job. I approved four spec corrections Claude proposed, and chose no frontend test framework in the repo. | `docker compose down -v && up --build` brings up everything. A real browser ran the spec's full demo with no reload: the spike, the host going down, Ack and Resolve clicks, and recovery from API and Redis outages. It found a real phone layout bug, and looking at screenshots found a lingering error message; both are fixed. All Python gates and `npm run build` pass. The CI workflow isn't verified on GitHub yet because nothing has been pushed. |
| 9. Tests | 2026-11-03 12:48:49 | 2026-11-03 13:20:52 | Complete the test suite. The required tests already existed, so Claude measured for gaps first, and I approved its plan. I asked Claude to push to GitHub, which ran CI for the first time and passed. | 246 tests pass, including under heavy CPU load in random order. A real Postgres outage found that the API returned 500s, and after two wrong attempts it now returns 503s. The flakiness run found a real flaky test, now fixed. A false host-down limitation was removed. |

The session was paused between 2026-11-01 20:28:38 and 2026-11-02 21:07:19, before Phase 2 started, between 2026-11-02 22:17:01 and 2026-11-03 09:52:06, before Phase 6 started, and between 2026-11-03 11:14 (approximately, when I paused after the first CI run) and 12:48:49, before Phase 9 started. Claude measured coverage and wrote the Phase 9 plan at 11:08 before that pause.

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
| 23 | 7 | The required idempotency test failed whenever the stack was running, because Redis pub/sub ignores DB numbers and the test received the simulator's events. Earlier runs only passed because the agent happened to be stopped. A reviewer running `docker compose up` and then `pytest` would have hit this. | Claude ran the test on purpose with the agent publishing: 3 failures out of 3 | The channel is now `events:{redis db}`. The full suite now passes with the agent running. |
| 24 | 7 | The first shutdown test called `server.handle_exit(SIGTERM)`. Uvicorn re-raised the signal after stopping, which killed pytest (exit 3). | Runtime behavior: pytest died partway through | The test sets `should_exit` instead. The real SIGTERM is checked on compose. |
| 25 | 7 | One line wasn't formatted as ruff requires, and a test helper had an async `timeout` parameter (ASYNC109). | `ruff format --check`, `ruff check` | Formatted the line and renamed the parameter. |
| 26 | 7 | **A false claim by Claude.** It proposed, and wrote in a code comment, that the subscription needs a connection without a read timeout because a quiet period would look like a failure. With a 2s read timeout added, a 2.5s quiet period still worked, because the blocking `listen()` doesn't apply that timeout. | Deliberate code break reported `MISSED` for 1 of 13; investigating showed the test was right and the claim was wrong | Corrected the comment. The quiet-period test stays as a regression check. |
| 27 | 7 | **Invalid demo evidence, three times.** (1) The first run tested the old image: its `up --build` output was hidden by `tail -1`, `/v1/events` returned 404, and the script carried on. It also took about 7 minutes and I don't know why, because that output was hidden. (2) `TaskStop` didn't stop the script, which kept running and overlapped the next rebuild. (3) The second run's output was garbled ("Binary file matches") because the script cleared a file `curl` was still writing to. | I noticed it was taking too long and asked; Claude then read the output and saw the 404. Reading the output caught (3). | The script now aborts unless `/v1/events` serves `resync`, and it strips NUL bytes. None of those runs' results are used below. |
| 28 | 7 | Not fixed: stopping the api with a stream open logs `ERROR: ASGI callable returned without completing response`. It exits 0 in about 1.6s and the client disconnects, so it looks cosmetic, but I didn't find the cause. | Reading the api log during the shutdown check | Left as is and recorded here. |
| 29 | 8 | Spec gaps: a host going down publishes no event, so with SSE alone its tile would stay green. nginx resolves the api's address once at startup, so a recreated api would mean 502s. And after an error response, the browser's `EventSource` stops retrying for good. | Claude's review while planning and writing the phase. Measured afterwards: `EventSource` was permanently closed after a 502, and a static `proxy_pass` returned 502 once the api had a new IP | Hosts are refetched every 5s, nginx looks the api up per request, and the page reconnects by itself. |
| 30 | 8 | The page scrolled sideways by 164px at phone width. The absolutely positioned hidden "Actions" header escaped the table's scroll container. | A browser check of `scrollWidth` at 390px | `position: relative` on the container. The check now passes. |
| 31 | 8 | A refused-action error stayed on screen indefinitely. It was still showing long after, in the Reconnecting state. | Claude looking at the screenshots, not an automated check | The message now clears itself after 8s. |
| 32 | 8 | The browser script called `innerText` on an SVG element, which aborted its first run. This was a script bug, not a dashboard bug. | Runtime error; the script stops at the first failure | Used `textContent`, and reran from a clean stack. |
| 33 | 8 | `App.tsx` had a clumsy workaround that set state during render to pick a default host. | Claude's own review, before the first browser run | The hook now works out the selected host directly. |
| 34 | 9 | During a real Postgres outage every data endpoint returned a plain `500`, with 128 tracebacks in about 45s of logs. Claude had never taken Postgres down on the running stack. | Runtime behavior: a measured outage on compose | They now return `503 {"detail":"database unavailable"}` with a one-line log entry. |
| 35 | 9 | **The first two fixes for #34 were wrong.** The first passed its tests but still returned 500 in a real outage, because the tests stubbed errors Claude had guessed. The real error was `socket.gaierror`: a stopped container's name stops resolving. Claude then narrowed the list on a false claim, stated as fact, that `DataError` is a kind of `InterfaceError`, which also broke tests. | Rerunning the real outage, reading the actual exceptions from the logs, and checking the class hierarchy with `issubclass` | The list now matches the measured errors, with `OSError` covering `gaierror`. The third real outage returned 503 on every endpoint. |
| 36 | 9 | `ts-too-new` was flaky. Its "+6 minutes" timestamp was worked out at test collection, and under load the suite takes 4 minutes, so by the time it ran the timestamp was back inside the 5-minute limit and rightly accepted. It failed 2 runs out of 2 under load, and could have failed in CI on a slow machine. | The flakiness run: heavy CPU load, random order | The timestamp is now worked out inside the test. A new test had the same hazard and was fixed too. |
| 37 | 9 | A known limitation Claude wrote in Phase 5 was never measured: false host-down incidents after a Postgres outage. The first outage run didn't show any, but only by timing luck. | Reasoning about tick timing after the measurement | A failed tick now restarts the host-down grace period, covered by a test, and the limitation is removed from `SPEC.md`. |
| 38 | 9 | Claude's outage script left a fake `probe` host in my local database, which would have gone down and opened a false incident. | Claude reading the output of its own script | Removed it, and the script now uses a real host. |
| 39 | 9 | A wrong type in the new test file (`redis.keys` is typed as bytes). | `mypy` | Fixed. |

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
- **A slow SSE client gets disconnected rather than silently losing events.** A lost `incident` event would leave the table wrong with no sign of it. Disconnecting makes the browser reconnect and refetch.
- **Every connection's first event is `resync`, sent again after the Redis subscription recovers.** The dashboard needs one rule, "on `resync`, refetch everything", and since the subscription is already live, nothing can be missed between the fetch and the stream.
- **`sse-starlette` instead of a hand-written stream**, for pings, disconnect handling and not blocking server shutdown. Shutdown with a stream open was measured at about 1.6s.
- **Known limit, accepted:** a subscription connection that dies silently, with no TCP close, isn't detected, because there is no read timeout. A Redis restart closes the connection, so it recovers in about 3s.
- **Hosts are refetched every 5s on top of SSE**, because a host going down publishes no event. It's a little extra traffic in exchange for the tile being right.
- **The dashboard shows Live or Reconnecting, with a banner when updates have stopped.** A monitoring page that silently goes stale looks healthy when it isn't.
- **The chart breaks its line across gaps over 10s** rather than drawing through time with no data, and it has a fixed 0–100 axis so a small wobble doesn't look like a spike.
- **Status never relies on color alone:** tiles and incidents always show a word and an icon.
- **No frontend test framework in the repo (my choice).** Claude verified the page with a one-off Playwright script that isn't committed, so CI only checks that the frontend compiles. Frontend regressions wouldn't be caught automatically. It's listed as a known limitation.
- **While Postgres is unreachable, data endpoints return 503.** `/healthz`, the dashboard page and the SSE stream stay up, and the agent logs failures and carries on. Metrics sent during an outage are lost, not queued, so the chart shows a gap.
- **One gap in the tests is accepted:** `rules.py:104` runs only if a rule is deleted between two statements, and a test would need heavy mocking to prove very little.
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

**Phase 7** (same environment):
- `ruff format --check .` and `ruff check .`: failed once (problem #25), then passed. `mypy`: no issues in 35 source files.
- `pytest -q` **with the agent publishing**: 212 passed in about 73s. Before the fix, the idempotency test failed 3 out of 3 times in this situation.
- SSE tests run against a real uvicorn server on a free port, because httpx's in-process transport can't read a stream that never ends.
- Deliberate breaks: 13 made one at a time and restored. 12 were caught. The 13th showed that Claude's claim was false, not that the test was weak (problem #26).
- Not tested: `send_timeout` for a client that stops reading. The overflow path is tested, but a truly stalled TCP client isn't.
- On compose, after the script was fixed:
  - `curl -N /v1/events`: `resync` first, then `metrics` events (10 in 5s).
  - `--spike-cpu host-3`: an `incident` event with status `open` arrived on the stream 34s after the spike started. After `POST ack`, a second event arrived with status `acknowledged`.
  - A second client connecting and disconnecting left the first one receiving, with 1 Redis subscriber throughout. Client cleanup itself is shown by `test_disconnecting_removes_the_client`, not on compose.
  - `docker compose restart redis`: the same curl process stayed connected, `resync` arrived within about 3s, and `metrics` events resumed. The api logged `subscription lost` and then `subscription restored`.
  - `docker compose stop api` with a stream open: 1.63s, exit 0, and curl ended. Logged the error in problem #28.
- Keep-alive pings: a fresh connection with the agent stopped got pings 15s and 30s after connecting. One earlier 17s window in the demo showed none. I think that was caused by the script clearing its output file, but I didn't confirm it.

**Phase 8** (same environment, Node 24.21.0, Chromium through Playwright):
- `npm run build` (strict TypeScript, then Vite): passes. `npm audit --omit=dev`: 0 vulnerabilities.
- Python gates with the stack running: `ruff format --check .` and `ruff check .` pass, `mypy` has no issues, and `pytest -q` has 212 passed in about 74s.
- `docker compose down -v && docker compose up --build -d --wait`: exit 0 in 24s with cached image layers. A first build without the cache will take longer, and I didn't time that. `--wait` now waits for the api's healthcheck.
- Browser run against `http://localhost:8080` on a clean stack, final result `ALL PASSED`, with a marker confirming the page was never reloaded:
  - Healthy: Live, 5 hosts up, the chart moving by itself, no incidents.
  - `--spike-cpu host-3 --stop host-2`: the host-3 incident appeared at 34s, with the chart at 99%. host-2's tile turned red and said Down at 52s, and its incident appeared at 54s. The other 4 stayed up. The chart tooltip works.
  - Real clicks: Ack, then Resolve. It stayed resolved through an engine tick with CPU still high.
  - An action on an already-resolved incident showed `cannot move incident from resolved to acknowledged`, refetched, and the message cleared by itself.
  - Normal agent restored: 5 hosts up.
  - API stopped: Reconnecting and a stale-data banner. After restarting: Live again in 2s, banner gone.
  - API recreated, and Redis restarted: the chart kept updating and the indicator was Live.
  - 390px wide in dark mode: no horizontal page scroll.
- Screenshots Claude looked at: fault state, Reconnecting state and phone dark mode.
- Browser console errors: only the expected ones (a request the script blocked on purpose, the deliberate 409, and failed requests during the outage). None during the Redis restart, and no page errors.
- nginx comparison, with the api forced onto a new IP: static `proxy_pass` went from 200 to 502; `web/nginx.conf` stayed at 200.
- Not covered: the deliberate-break checks used in earlier phases, because there is no frontend test suite to run them against.

**CI:** the first push to GitHub ran [CI run 41570702745](https://github.com/JerW18/Monitoring-and-Incident-Dashboard/actions/runs/41570702745), which passed both jobs at commit `f4ac0eb`: `python` (install, ruff, mypy, pytest with Postgres and Redis service containers) and `web` (`npm ci`, build). It was the first time the tests ran on Linux. The Phase 9 changes aren't on GitHub yet at the time of writing, so CI hasn't checked them.

**Phase 9** (same environment):
- `ruff format --check .` and `ruff check .` pass, and `mypy` has no issues in 36 source files, after fixing problem #39.
- `pytest -q` with the stack running: 246 passed in about 99s (it was 212 before this phase).
- Flakiness: under heavy CPU load (48 busy processes on 24 CPUs), in random order, with the stack running. Before the fixes, 2 runs had 6 failures each (problems #35 and #36). After the fixes, 1 run had 246 passed in about 237s.
- Coverage (one-off, not in the repo): 97% across lines and branches. What's left: `agent/__main__.py` shows 0% because it now runs in a subprocess, which coverage doesn't follow; two loop-exit branches; and `rules.py:104`.
- Deliberate breaks: 11 made one at a time and restored, all caught. They cover the engine and hub starting with the app, ack events, each kind of Redis TTL, the resolve race, the agent as a process, the 503 handler, and the restarted grace period.
- New tests worth noting: the full chain over real HTTP and SSE with the engine running by itself; every Redis key having a TTL after real activity; a person and the engine resolving at the same moment; `python -m agent` as a real process.
- Real Postgres outage on compose, stopped for about 45s (longer than the TTL), with the agent running. Final run:
  - During: `/healthz` 503; `POST /v1/metrics`, `/v1/hosts`, `/v1/incidents` and `/v1/rules` all `503 {"detail":"database unavailable"}`; dashboard page 200; SSE stream connected.
  - After: `/healthz` 200 in under 1s, ingest 202, all 5 hosts up, and rows growing. Incidents stayed at 11, so no false ones opened.
  - API log tracebacks fell from 128-130 to 15. 9 of those are the engine's failed ticks, logged on purpose; I didn't look at the other 6.

## Reviewer notes from the human

