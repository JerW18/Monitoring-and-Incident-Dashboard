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
| Spec | 2026-11-01 20:10:17 | | Rewrite the spec for a Python backend, update `CLAUDE.md`, delete irrelevant files, start from scratch | `SPEC.md` and `CLAUDE.md` rewritten for Python (FastAPI, asyncpg, Alembic). Old files deleted. Waiting for my approval. |

## Problems found in AI output

_None yet._

## Decisions and trade-offs

- **SSE instead of WebSocket:** traffic only goes server→client, SSE has built-in browser reconnect, and it passes through an nginx proxy easily. Ack and resolve are plain POST requests.
- **Where state lives:** Postgres holds hosts, metrics, rules and incidents. Redis holds only `host:{id}` (TTL 30s, expiry means the host is down), `cooldown:{rule}:{host}` (TTL = rule cooldown) and the pub/sub channel.
- **One active incident per host+rule:** the partial unique index covers both `open` and `acknowledged`, because an acknowledged incident is still active.
- **asyncpg with raw SQL, no ORM:** the important queries (`UNNEST` batch insert, `ON CONFLICT DO NOTHING`, compare-and-swap status update) stay visible. Alembic is used only for migrations.
- **One Redis pubsub listener fanning out to per-client bounded queues**, instead of one Redis connection per SSE client. A slow client drops events instead of blocking the others.
- **pip + venv with pinned `requirements.txt` instead of uv:** I chose plain pip, which needs no extra tool installed. The cost is no lockfile for transitive dependencies; only top-level packages are pinned with `==`.
- **A single uvicorn worker**, so there is one alert engine per container. DB idempotency keeps duplicate engines safe anyway.

## Verification

_Filled in as phases complete._

## Reviewer notes from the human

