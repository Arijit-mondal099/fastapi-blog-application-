# Missing ADRs

`docs/adr/` did not exist when the Phase 1 (users and auth) implementation began.
No ADR-0001 through ADR-0008 files were present in the repository or recoverable
from git metadata.

The following decision records are missing:

- ADR-0001: Multi-author platform with three roles
- ADR-0002: Authors are admin-granted; `ADMIN_EMAILS` bootstrap
- ADR-0003: Short-lived access JWT plus rotating refresh token
- ADR-0004: Cookie delivery, SameSite=Strict
- ADR-0005: Backend-driven Google OAuth keyed by `sub`
- ADR-0006: Direct browser-to-ImageKit upload
- ADR-0007: ARQ with Redis; outbox via `pending_delete`
- ADR-0008: v1 feature scope

This file does not replace or invent ADR content. Per explicit authorization, the
Phase 1 (users and auth) implementation used only the decision index and sections
3 to 5 of `docs/ARCHITECTURE.md`, `docs/CONTEXT.md`, `AGENTS.md`, and the confirmed
decisions supplied with the Phase 1 task.

Phase 2 (roles and admin) also proceeded against the decision index and the rules
supplied with that task because these ADR files remain absent.

Phase 3 (posts, tags, and public reads) also proceeded against the decision index
and the rules supplied with that task because these ADR files remain absent.

Phase 4 (full-text search) also proceeded against the decision index and the rules
supplied with that task because these ADR files remain absent.

Phase 5 (comments) also proceeded against the decision index and the rules
supplied with that task because these ADR files remain absent.
