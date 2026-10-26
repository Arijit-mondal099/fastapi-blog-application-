# Architecture: Blog Backend

Status: v1 design. Decisions live in `adr/`. Terms live in `CONTEXT.md`.

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
  created_at
```

Indexes that matter: `posts(status, published_at desc, id)` for cursor pagination; GIN on `search_vector`; `posts(author_id)`; `comments(post_id, created_at)`; `refresh_tokens(family_id)`; `media(status, created_at)` for the orphan sweep.

Phase 3 implements `posts`, `tags`, and `post_tags` without `cover_media_id` or `search_vector`; those columns arrive with media (Phase 6) and search (Phase 4).

Open schema question: `media.status` includes `pending_delete` for the outbox pattern (ADR-0007), which is still a proposal.

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
1. `GET /media/upload-auth` returns signed params (signature, token, expire) and allowed types and size.
2. The browser uploads the file to ImageKit and gets `file_id` and `url`.
3. `POST /media` (confirm) records a Media row with status `pending`.
4. `POST /posts` attaches the Media by `cover_media_id`. The service checks that the Media's owner is the caller.
5. The worker job `verify_media` checks the file through the ImageKit API, then sets status `verified`.

### 4.5 Delete a post (ADR-0007, outbox proposed)
1. In one DB transaction: delete or archive the Post, and set its Media rows to `pending_delete`.
2. After commit, enqueue `delete_remote_media` for each Media.
3. The job calls ImageKit, retries on failure, and marks the row `deleted` on success. Jobs are idempotent.
4. The scheduled `sweep_orphans` re-enqueues any row still in `pending_delete`, and deletes `pending` rows older than 24 hours that are unreferenced.

### 4.6 Public reads, post management, and search
- `GET /posts`: published only, cursor pagination on `(published_at, id)`, filters `tag` (tag slug) and `author` (author id), sort newest first.
- `GET /posts/{slug}`: published posts are public. A draft or archived post is returned only to its owner or an Admin; every other caller gets 404.
- `GET /tags`: every tag with a count of published posts only.
- `POST /posts`, `PATCH /posts/{id}`, publish, archive, and delete require the owner or an Admin. A non-owner gets 403 when the post is published and 404 when it is a draft or archived post they cannot see. `GET /me/posts` returns the caller's own posts in every status.
- `GET /search?q=`: `websearch_to_tsquery` over `search_vector`, ranked, paginated. Planned for Phase 4.
- Anonymous reads hit Postgres directly. Caching is a Phase 8 concern.

### 4.7 Admin user management
- `GET /admin/users`: admin only, cursor pagination on `(created_at, id)` newest first, returning `id`, `email`, `name`, and `role`. Never returns `google_sub`.
- `PATCH /admin/users/{id}/role`: admin only. The caller's role is re-read from the DB rather than trusted from the access token. Self-demotion returns 409, an unknown user returns 404, and a same-role change is a 200 no-op.
- A real role change updates the user and revokes every refresh-token family for that user in one transaction, then emits a structured audit log line with `actor_id`, `target_id`, `old_role`, and `new_role`. Access tokens still run until their normal expiry.

## 5. Background jobs (ADR-0007)

| Job | Trigger | Retries | Idempotency |
|-----|---------|---------|-------------|
| `delete_remote_media` | enqueued after post or user delete | 5, exponential backoff | ImageKit delete ignores already-deleted files |
| `verify_media` | enqueued after Media confirm | 3 | Re-check sets the same status |
| `sweep_orphans` | cron, hourly | none (next run retries) | Only acts on rows in the target state |
| `purge_expired_refresh_tokens` | cron, daily | none | Deletes rows where `expires_at < now()` |

## 6. Cross-cutting concerns
- **Config:** pydantic-settings, environment only. Secrets never in the repo. See `.env.example`.
- **Errors:** domain exceptions map to HTTP codes in one place (`core/errors.py`). Responses never leak stack traces.
- **Logging:** structured JSON with a request ID propagated to jobs.
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
| 0007 | ARQ with Redis; outbox via `pending_delete` | ARQ accepted; outbox proposed |
| 0008 | v1 feature scope | Accepted |

## 9. Open items
1. Confirm the outbox pattern in ADR-0007 (affects `media.status` and the sweep job).
2. Confirm the frontend and API share a site. If not, ADR-0004 changes.
3. Choose the hosting provider (affects managed Postgres and Redis choice).