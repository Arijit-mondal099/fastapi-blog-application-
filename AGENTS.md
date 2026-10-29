# AGENTS.md

Guidance for AI coding agents (OpenCode, Claude Code, or similar) working in this repo.
Read `docs/ARCHITECTURE.md` and `docs/CONTEXT.md` before changing code. Decisions are in `docs/adr/`.

## Project

FastAPI blog backend: multi-author posts, Google sign-in, cookie-based JWT access and refresh tokens, ImageKit media via direct browser upload, ARQ background jobs on Redis, PostgreSQL via async SQLAlchemy 2.x and Alembic.

Status: Phases 0-5 implemented (skeleton, auth, roles/admin, posts and tags, search, comments). Media, background jobs, and other later phases are pending. Commands below are the target setup. If one doesn't exist yet, say so and don't invent output.

## Commands

```
docker compose up --build          # api, worker, postgres, redis
alembic upgrade head               # apply migrations
alembic revision --autogenerate -m "describe change"
pytest                             # unit + integration (integration uses Postgres via testcontainers)
pytest tests/unit                  # fast loop
ruff check . && ruff format --check .
mypy app
```

Run a single test: `pytest path/to/test_file.py::test_name`

## Architecture rules (non-negotiable)

- Layering: `api/v1` (routes) -> `services` -> `repositories` and `integrations`. Routes never touch the DB session or ImageKit directly.
- Services hold business rules and know nothing about HTTP. Repositories hold queries and no rules.
- Jobs (`app/jobs/`) call services, not repositories. Keep one rule set shared by API and worker.
- Auth identity is Google `sub`, never email. Only `email_verified=true` accounts are accepted.
- Refresh tokens are stored as hashes only. Rotation on every use. Reuse of a revoked token revokes the whole `family_id`.
- Tokens travel only in httpOnly, Secure, SameSite=Strict cookies. Never return tokens in JSON or store them in localStorage. (ADR-0004)
- Role checks: use `require_role`. Admin role changes and deletes re-read the role from the DB.
- Media uploads go browser -> ImageKit with backend-signed params. Do not add a route that accepts file bytes. (ADR-0006)
- Background jobs must be idempotent. Assume every job can run twice.
- Schema changes go through Alembic migrations only. Never edit `create_all` or the DB by hand.

If a task conflicts with an accepted ADR, stop and say so. Propose a new ADR rather than silently diverging.

## Coding style

- Python 3.12+, type hints on all public functions, pydantic v2 schemas.
- Async everywhere on the request path and in jobs. No blocking I/O in async functions.
- Errors: raise domain exceptions from services; map them to HTTP in `core/errors.py` only.
- Never leak stack traces or internal IDs in error responses beyond what the API spec promises.
- Config comes from `app/core/config.py` (pydantic-settings). No `os.environ` reads elsewhere.
- Logging is structured JSON with request ID. No `print`.

## Working rules for agents

1. **Think before coding.** State assumptions. If the request is ambiguous, ask one focused question before writing code.
2. **Simplicity first.** Build what was asked. No speculative features, no abstractions for one use, no configurability nobody requested. If a change is growing past what the task needs, simplify it.
3. **Surgical changes.** Edit only what the task requires. Don't reformat, rename, or refactor unrelated code. Match existing style even where you'd choose differently. If you spot unrelated dead code, mention it; don't delete it.
4. **Clean up your own mess.** Remove imports and helpers your change made unused. Leave pre-existing dead code alone unless asked.
5. **Verify with tests.** For bugs, write a failing test first, then fix it. For features, add tests for the invalid and permission-denied paths, not just the happy path. Run the relevant test subset before reporting done.
6. **Report honestly.** Say what you ran, what passed, and what you could not verify. Don't claim a phase is complete unless its verify checklist (in `docs/ARCHITECTURE.md` and the phase plan) passes.

## Testing expectations

- Services: unit tests with fake repositories.
- Repositories and migrations: integration tests against real Postgres.
- Auth: cover invalid `state`, unverified email, refresh rotation, reuse detection, demoted user losing privileges.
- Permissions: a matrix test per actor (reader, author, owner-author, other-author, admin, anonymous) for each protected endpoint.
- Jobs: run the job function twice in a test and assert no extra side effects.
- Never call real Google or ImageKit in tests. Mock the integrations layer.

## Things not to do

- Don't add likes, view counters, notifications, nested comments, RSS, or scheduled publishing. They are out of v1 (ADR-0008).
- Don't add Elasticsearch or another search engine. Use Postgres full-text (ADR-0008).
- Don't switch to Celery, `BackgroundTasks`, or a Postgres queue without a new ADR (ADR-0007).
- Don't commit `.env`, secrets, or real ImageKit or Google keys. Update `.env.example` when adding a config key.
- Don't disable a lint or type check to make CI pass. Fix the cause.
- Don't edit an accepted ADR's decision. Add a new ADR that supersedes it.

## Documentation duties

- New domain term: add it to `docs/CONTEXT.md` in the same change.
- New architectural decision: add `docs/adr/NNNN-title.md` with Status, Context, Decision, Consequences.
- Changed endpoint or flow: update `docs/ARCHITECTURE.md`.
- New config key: update `.env.example`.

## Open questions (check before building the affected part)

1. Outbox pattern for media deletion (ADR-0007) is proposed, not accepted. Confirm before implementing `pending_delete`.
2. Frontend and API site setup. If they're on different domains, cookie config (ADR-0004) must change first.
3. Hosting provider, which affects deployment config.
