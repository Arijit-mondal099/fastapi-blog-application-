"""Add full-text search to posts.

Revision ID: 0004_post_search
Revises: 0003_posts_and_tags
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_post_search"
down_revision: str | Sequence[str] | None = "0003_posts_and_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR_FUNCTION = """
CREATE FUNCTION posts_search_vector_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.body_md, '')), 'B');
    RETURN NEW;
END;
$$
"""

SEARCH_VECTOR_TRIGGER = """
CREATE TRIGGER trg_posts_search_vector_update
BEFORE INSERT OR UPDATE OF title, body_md ON posts
FOR EACH ROW
EXECUTE FUNCTION posts_search_vector_update()
"""

BACKFILL_SEARCH_VECTOR = """
UPDATE posts
SET search_vector =
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(body_md, '')), 'B')
"""


def upgrade() -> None:
    op.add_column("posts", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.execute(SEARCH_VECTOR_TRIGGER)
    op.execute(BACKFILL_SEARCH_VECTOR)
    op.create_index(
        "ix_posts_search_vector",
        "posts",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_posts_search_vector", table_name="posts")
    op.execute("DROP TRIGGER IF EXISTS trg_posts_search_vector_update ON posts")
    op.execute("DROP FUNCTION IF EXISTS posts_search_vector_update()")
    op.drop_column("posts", "search_vector")
