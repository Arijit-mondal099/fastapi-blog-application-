"""Create media and add posts.cover_media_id.

Revision ID: 0006_media
Revises: 0005_comments
Create Date: 2026-10-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_media"
down_revision: str | Sequence[str] | None = "0005_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    media_status = postgresql.ENUM(
        "pending",
        "verified",
        "pending_delete",
        "deleted",
        name="media_status",
        create_type=False,
    )
    media_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("imagekit_file_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            media_status,
            server_default=sa.text("'pending'::media_status"),
            nullable=False,
        ),
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
            ["owner_id"],
            ["users.id"],
            name="fk_media_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media"),
        sa.UniqueConstraint("imagekit_file_id", name="uq_media_imagekit_file_id"),
    )
    op.create_index(
        "ix_media_status_created_at",
        "media",
        ["status", "created_at"],
        unique=False,
    )

    op.add_column(
        "posts",
        sa.Column("cover_media_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_posts_cover_media_id_media",
        "posts",
        "media",
        ["cover_media_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_cover_media_id_media", "posts", type_="foreignkey")
    op.drop_column("posts", "cover_media_id")
    op.drop_index("ix_media_status_created_at", table_name="media")
    op.drop_table("media")
    op.execute("DROP TYPE IF EXISTS media_status")
