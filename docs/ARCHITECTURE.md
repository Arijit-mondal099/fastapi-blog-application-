# Architecture: Blog Backend

Status: Phases 0-7 implemented; v1 design. Decisions live in `adr/`. Terms live in `CONTEXT.md`. Phase 8 (hardening) and Phase 9 (deployment) remain.

## 1. System overview

```
              +-----------------------+
 Browser ---> |  Frontend (Next.js)   |
   |  ^      +-----------------------+
   |  | cookies (httpOnly, SameSite=Strict)
   |  |   JSON responses
   |  v
   |  +-----------------------+        +-----------------+
   +->|  api  (FastAPI)       |------->|  Google OAuth   |
      |  routes -> services   |        +-----------------+
      |  -> repositories      |
      +----+-------------+----+        +-----------------+
           |             |             |  ImageKit       |
           |             | enqueue     |  (media store)  |
           v             v             +--------^--------+
      +---------+   +---------+                |
      |Postgres |   |  Redis  |<--- jobs ---+  | delete / verify
      +---------+   +----+----+             |  |
           ^             |                  |  |
           |             v                  |  |
           +------- worker (ARQ) -----------+--+
                    cron + retries
```

Browser to ImageKit (direct, for uploads): the browser uploads files using params signed by `api`. Files never pass through our servers.

Two processes come from one codebase: `api` (FastAPI, serves HTTP) and `worker` (ARQ, runs Jobs). Both share models, services, and the DB session code.

## 2. Components and responsibilities

| Layer | Path | Responsibility | Must not |
|-------|------|----------------|----------|
| Routes | `app/api/v1/` | Parse input, call one service, shape response, set cookies | Touch the DB or ImageKit directly |
| Schemas | `app/schemas/` | Request and response validation | Hold business logic |
| Services | `app/services/` | Business rules: authorization, state transitions, token rotation, orchestration | Know about HTTP |
| Repositories | `app/repositories/` | Queries and persistence | Apply business rules |
| Integrations | `app/integrations/` | Google OAuth, ImageKit SDK wrappers | Be called from routes |
| Jobs | `app/jobs/` | ARQ task functions and cron schedules | Contain business rules that the API cannot also reach |
| Core | `app/core/` | Config, security (JWT), dependencies, error mapping | Hold feature logic |

Dependency direction: routes -> services -> repositories and integrations. Jobs call services too, so both processes share one rule set.

## 3. Data model (v1)

Derived from ADR-0001 to ADR-0008. Column lists are the planned minimum.

```
users
  id              uuid pk
  google_sub      text unique not null      -- identity key (ADR-0005)
  email           citext not null           -- refreshed on login, not an identity key
  name            text
  avatar_url      text
  role            enum(reader, author, admin) not null default 'reader'
  created_at, updated_at

refresh_tokens                                -- hash only, never the token (ADR-0003)
  id              uuid pk
  user_id         uuid fk users
  family_id       uuid not null             -- one login session
  token_hash      text unique not null
  expires_at      timestamptz not null
  revoked_at      timestamptz null
  replaced_by     uuid null fk refresh_tokens
  created_at

media                                         -- ADR-0006
  id              uuid pk
  owner_id        uuid fk users
  imagekit_file_id text unique not null
  url             text not null
  mime_type       text
  size_bytes      int
  status          enum(pending, verified, pending_delete, deleted)
  created_at, updated_at

posts                                         -- ADR-0008
  id              uuid pk
  author_id       uuid fk users not null
  slug            text unique not null
  title           text not null
  body_md         text not null
  status          enum(draft, published, archived) not null default 'draft'
  cover_media_id  uuid null fk media
  search_vector   tsvector                   -- GIN index, maintained by trigger
  published_at    timestamptz null
  created_at, updated_at

tags
  id, name text unique, slug text unique

post_tags
  post_id fk posts, tag_id fk tags, primary key (post_id, tag_id)

comments                                      -- flat, no threading
  id              uuid pk
  post_id         uuid fk posts on delete cascade
  author_id       uuid fk users
  body            text not null
  created_at, updated_at
```

Indexes that matter: `posts(status, published_at desc, id)` for cursor pagination; GIN on `search_vector`; `posts(author_id)`; `comments(post_id, created_at, id)` for comment listing; `comments(author_id)`; `refresh_tokens(family_id)`; `media(status, created_at)` for the orphan sweep.

Phase 3 implements `posts`, `tags`, and `post_tags`. Phase 4 adds `search_vector`, its GIN index, and the trigger that maintains it. Phase 5 implements `comments`. Phase 6 implements `media` and `posts.cover_media_id`. Phase 7 implements media verification, media deletion, and the outbox pattern.

`media.status` includes `pending_delete` for the outbox pattern (ADR-0007), implemented in Phase 7.

## 4. Key flows

### 4.1 Google login (ADR-0005)
1. `GET /auth/google/login`: create `state` and PKCE verifier, store them in a short-lived signed SameSite=Lax cookie, redirect to Google.
2. `GET /auth/google/callback`: check `state`, exchange the code, verify the ID token (`iss`, `aud`, `exp`, signature), require `email_verified`. The state cookie is cleared on success and failure.
3. Upsert the user by `google_sub`. On first login, grant `admin` if the email is in `ADMIN_EMAILS`, otherwise `reader`.
4. Issue access and refresh tokens. Set both as httpOnly, Secure, SameSite=Strict cookies (ADR-0004). The refresh cookie is scoped to `Path=/auth/refresh`.
5. Redirect to `FRONTEND_URL + POST_LOGIN_PATH`. The frontend calls `/auth/me` client-side, because Strict cookies are not sent on a cross-site redirect.

### 4.2 Token refresh and reuse detection (ADR-0003)
1. `POST /auth/refresh` with the refresh cookie.
2. Look up the hash. If it is revoked, revoke the entire `family_id` and return 401. This is reuse, which means a token was stolen.
3. If it is valid, mark it revoked with `replaced_by` set to the new token, issue a new pair in the same family, and set the cookies.
4. `POST /auth/logout` always clears both cookies. If the access token signature is valid, even when expired, it revokes that token's `family_id` only.

### 4.3 Authorization
- The access token carries `sub`, `role`, and `family_id`. Route dependencies (`require_role`) read the role from the token.
- Admin routes that change roles or delete content re-read the role from the DB, so a demotion takes effect at once there.
- Ownership: Authors can change only their own Posts. Comments are deletable by the comment author, the post author, or an Admin.
- Effective role changes reach regular routes within one access-token lifetime (about 15 minutes).

### 4.4 Create a post with a cover image (ADR-0006)
1. `GET /media/upload-auth` returns signed params (signature, token, expire), the public key, a per-user upload folder, and a backend-chosen file name. The expiry defaults to 300 seconds and is clamped to 3600. Only authors and admins may request it.
2. The browser uploads the file to ImageKit and gets `file_id` and `url`.
3. `POST /media` (confirm) records a Media row with status `pending`, owned by the caller. It rejects an empty MIME type or a size of zero or less.
4. `PATCH /posts/{id}` attaches the Media by `cover_media_id`, or clears it with `null`. The service checks that the Post is owned by the caller or the caller is an Admin, and that the Media is owned by the caller or the caller is an Admin; an unowned Media returns 404 so its existence is not revealed.
5. The worker job `verify_media` checks the file through the ImageKit API, then sets status `verified` when the MIME type is in `MEDIA_ALLOWED_MIME_TYPES` and the size is within `MEDIA_MAX_SIZE_BYTES`. A rejected file moves to `pending_delete` and a `delete_remote_media` job removes it; it becomes `deleted` only after ImageKit confirms the delete. A file missing from ImageKit is recorded as `deleted`. The upload-auth signature still does not bind file type, size, folder, or file name, so those values are guidance at upload time; the job enforces the MIME and size rules after upload.

### 4.5 Delete a post (ADR-0007, outbox confirmed)
1. In one DB transaction: delete the Post and set its cover Media to `pending_delete`.
2. After commit, enqueue `delete_remote_media` for each Media. An enqueue failure is logged and does not fail the request.
3. The job calls ImageKit, retries with exponential backoff, and marks the row `deleted` on success. An ImageKit 404 for an already-missing file counts as success. Jobs are idempotent.
4. The scheduled `sweep_orphans` re-enqueues any row still in `pending_delete`, and marks unreferenced `pending` rows older than 24 hours as `pending_delete` before enqueueing their deletion.

### 4.6 Public reads, post management, and search
- `GET /posts`: published only, cursor pagination on `(published_at, id)`, filters `tag` (tag slug) and `author` (author id), sort newest first.
- `GET /posts/{slug}`: published posts are public. A draft or archived post is returned only to its owner or an Admin; every other caller gets 404.
- `GET /tags`: every tag with a count of published posts only.
- `POST /posts`, `PATCH /posts/{id}`, publish, archive, and delete require the owner or an Admin. A non-owner gets 403 when the post is published and 404 when it is a draft or archived post they cannot see. `GET /me/posts` returns the caller's own posts in every status.
- `GET /search?q=`: public search over `search_vector` using `websearch_to_tsquery('english', q)`. Title is weighted A and `body_md` B. Only published posts with a `published_at` are searchable. Results are ranked by `ts_rank` descending, then `published_at` descending, then `id` descending, and paginated by an opaque cursor containing all three. `q` is trimmed and must be 1 to 200 characters; a stop-word-only query returns an empty page. `limit` defaults to 20 and is capped at 50. Results return `id`, `title`, `slug`, `excerpt` (first 200 characters of `body_md`), `published_at`, and `rank`, never `body_md`.
- Anonymous reads hit Postgres directly. Caching is a Phase 8 concern.

### 4.7 Admin user management
- `GET /admin/users`: admin only, cursor pagination on `(created_at, id)` newest first, returning `id`, `email`, `name`, and `role`. Never returns `google_sub`.
- `PATCH /admin/users/{id}/role`: admin only. The caller's role is re-read from the DB rather than trusted from the access token. Self-demotion returns 409, an unknown user returns 404, and a same-role change is a 200 no-op.
- A real role change updates the user and revokes every refresh-token family for that user in one transaction, then emits a structured audit log line with `actor_id`, `target_id`, `old_role`, and `new_role`. Access tokens still run until their normal expiry.

### 4.8 Comments (Phase 5)
- `GET /posts/{slug}/comments`: public for a post the caller can see. Cursor pagination on `(created_at, id)` ascending, oldest first; the cursor is opaque. `limit` defaults to 20 and is capped at 50. On a draft or archived post, only the post author or an Admin gets a page; every other caller gets 404.
- `POST /posts/{slug}/comments`: any signed-in user. The body is trimmed plain text of 1 to 2000 characters; empty or over-long bodies return 422. It is stored and returned verbatim, never rendered as HTML.
- Comments can be created only on published posts. A draft or archived slug returns 404 to every caller on create, including the post author and an Admin. Anonymous callers get 401 from authentication before the post is resolved.
- `DELETE /comments/{id}`: the comment author, the post author, or an Admin; every other signed-in caller gets 403. The delete is hard. A deleted comment returns 404 to everyone afterward.
- Deleting a Post cascades to its comments through the foreign key.

## 5. Background jobs (ADR-0007)

| Job | Trigger | Retries | Idempotency |
|-----|---------|---------|-------------|
| `delete_remote_media` | enqueued after post delete; re-enqueued by the sweep | 5, exponential backoff | Already-deleted rows are skipped; ImageKit 404 counts as success |
| `verify_media` | enqueued after Media confirm | 3, exponential backoff | Only acts on rows still `pending`; re-checks set the same status |
| `sweep_orphans` | cron, hourly at minute 0 | none (next run retries) | Only acts on rows in the target state |
| `purge_expired_refresh_tokens` | cron, daily at 03:00 | none | Deletes rows where `expires_at < now()`, plus revoked rows past the refresh-token retention window |

Jobs call services, never repositories. Job functions accept an optional originating request ID and bind it to the logging context, so worker log lines carry the same `request_id` as the HTTP request that enqueued them. Job arguments are IDs, never ORM objects.

## 6. Cross-cutting concerns
- **Config:** pydantic-settings, environment only. Secrets never in the repo. See `.env.example`.
- **Errors:** domain exceptions map to HTTP codes in one place (`core/errors.py`). Responses never leak stack traces.
- **Logging:** structured JSON. The API accepts `X-Request-ID` (up to 128 characters) or generates one, returns it in the response, and passes it to enqueued jobs so worker logs keep the originating ID.
- **Security:** CORS allows only the frontend origin with credentials. Auth routes are limited to 60 requests per minute per client IP and route, backed by Redis, and fail closed with 503 if Redis is unavailable. `X-Forwarded-For` is trusted only when `TRUSTED_PROXY_COUNT > 0`. Strict cookies and non-GET methods for state changes provide CSRF protection.
- **Migrations:** Alembic only. The release step runs `alembic upgrade head` before new code takes traffic.
- **Testing:** unit tests for services. Integration tests against real Postgres (testcontainers). Job tests run the worker function directly, including a double-run idempotency test.
- **Health:** `GET /health` returns `{"status": "ok"}` for liveness checks.

## 7. Deployment topology
- Containers: `api`, `worker`, Postgres, Redis. Locally with Docker Compose.
- Production: managed Postgres and Redis, two services from one image (different start commands), migrations as a release step.
- Per-environment Google OAuth redirect URIs and ImageKit keys.

## 8. Decision index

| ADR | Decision | Status |
|-----|----------|--------|
| 0001 | Multi-author platform with three roles | Accepted |
| 0002 | Authors are admin-granted; `ADMIN_EMAILS` bootstrap | Accepted |
| 0003 | Short-lived access JWT plus rotating refresh token | Accepted |
| 0004 | Cookie delivery, SameSite=Strict | Accepted, revisit if domains split |
| 0005 | Backend-driven Google OAuth keyed by `sub` | Accepted |
| 0006 | Direct browser-to-ImageKit upload | Accepted |
| 0007 | ARQ with Redis; outbox via `pending_delete` | Accepted; implemented in Phase 7 |
| 0008 | v1 feature scope | Accepted |

## 9. Open items
1. Confirm the frontend and API share a site. If not, ADR-0004 changes.
2. Choose the hosting provider (affects managed Postgres and Redis choice).