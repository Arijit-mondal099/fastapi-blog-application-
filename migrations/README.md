# Alembic migrations

Migrations use the async SQLAlchemy engine and read `DATABASE_URL` from
`app.core.config.Settings`.

```
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```
