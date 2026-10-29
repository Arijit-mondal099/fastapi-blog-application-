# Glossary (domain language)

Terms used consistently across code, API, and docs. Updated as decisions are made.

| Term | Meaning |
|------|---------|
| **User** | A person with an account, created on first Google sign-in. |
| **Role** | One of `reader`, `author`, `admin`. Every User has exactly one Role. |
| **Reader** | Default Role for every new User. Can read published Posts. |
| **Author** | Can create and manage their own Posts. Granted only by an Admin. |
| **Admin** | Can manage any Post and any User, including changing a User's Role. |
| **Bootstrap admin** | A User whose verified Google email is listed in `ADMIN_EMAILS`; gets `admin` on first login. |
| **Google identity** | A Google account identified by its stable `sub` claim, stored on the User. Email is not the identity key. |
| **Access token** | Short-lived JWT (about 15 min) carrying `sub`, `role`, and `family_id`. Sent on every API request. |
| **Refresh token** | Long-lived JWT with a unique `jti`; its hash is stored in Postgres, rotated on every use, and revocable. |
| **Session** | One login on one device, represented by a chain of rotated Refresh tokens. |
| **Media** | An image stored in ImageKit, recorded in our DB by ImageKit `file_id` and URL, owned by the User who uploaded it. |
| **Upload auth** | Short-lived signature, token and expiry issued by the backend so the browser can upload directly to ImageKit. |
| **Orphaned media** | Media not referenced by any Post or User after a grace period (24h). Deleted by a background job. |
| **Outbox** | A durable state written in the same DB transaction as the change that requires a remote side effect. For Media deletion, `pending_delete` is the outbox state; a Job performs the ImageKit delete after commit, and a periodic sweep re-enqueues unfinished rows. |
| **Job** | A unit of background work executed by the ARQ worker, with retries. Enqueued by the API or by a cron schedule. |
| **Worker** | The separate ARQ process that runs Jobs. Shares models and DB session code with the API. |
| **Post** | A blog article owned by exactly one Author. Lifecycle: `draft` → `published` → `archived`. Has a unique slug. |
| **Tag** | A label on Posts, many-to-many, created by Authors. |
| **Search** | Public ranked lookup over published Posts using Postgres full-text search on title and body. |
| **Comment** | A flat (non-threaded) reply to a Post by any signed-in User. |

_Out of v1 (deferred): likes, bookmarks, follows, view counts, nested comments, notifications, RSS, scheduled publishing, revision history._
