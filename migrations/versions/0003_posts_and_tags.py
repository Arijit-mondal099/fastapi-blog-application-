"""Create posts, tags, and their association table.

Revision ID: 0003_posts_and_tags
Revises: 0002_users_and_auth
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_posts_and_tags"
down_revision: str | Sequence[str] | None = "0002_users_and_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    post_status = postgresql.ENUM(
        "draft",
        "published",
        "archived",
        name="post_status",
        create_type=False,
    )
    post_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column(
            "status",
            post_status,
            server_default=sa.text("'draft'::post_status"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_posts_author_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_posts"),
        sa.UniqueConstraint("slug", name="uq_posts_slug"),
    )
    op.create_index("ix_posts_author_id", "posts", ["author_id"], unique=False)
    op.create_index(
        "ix_posts_status_published_at_id",
        "posts",
        [sa.text("status"), sa.text("published_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", postgresql.CITEXT(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
        sa.UniqueConstraint("slug", name="uq_tags_slug"),
    )

    op.create_table(
        "post_tags",
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["posts.id"],
            name="fk_post_tags_post_id_posts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name="fk_post_tags_tag_id_tags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("post_id", "tag_id", name="pk_post_tags"),
    )
    op.create_index("ix_post_tags_tag_id", "post_tags", ["tag_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_post_tags_tag_id", table_name="post_tags")
    op.drop_table("post_tags")
    op.drop_table("tags")
    op.drop_index("ix_posts_status_published_at_id", table_name="posts")
    op.drop_index("ix_posts_author_id", table_name="posts")
    op.drop_table("posts")
    op.execute("DROP TYPE IF EXISTS post_status")
