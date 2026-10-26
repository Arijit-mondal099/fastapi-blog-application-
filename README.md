# Blog Backend

FastAPI + PostgreSQL + SQLAlchemy backend for a multi-author blog. Google sign-in, JWT access and refresh tokens in cookies, ImageKit media, ARQ background jobs.

Start here:
- `docs/ARCHITECTURE.md`: system design, data model, flows
- `docs/CONTEXT.md`: domain glossary
- `docs/adr/`: decision records

## Local setup
```
cp .env.example .env     # fill in secrets
docker compose up --build
alembic upgrade head
```

API docs at `http://localhost:8000/docs` once running.
