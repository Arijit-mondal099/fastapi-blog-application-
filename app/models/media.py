"""Media records for files uploaded directly to ImageKit."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MediaStatus(StrEnum):
    """Media lifecycle states."""

    pending = "pending"
    verified = "verified"
    pending_delete = "pending_delete"
    deleted = "deleted"


def _status_values(status_type: type[MediaStatus]) -> list[str]:
    return [status.value for status in status_type]


class Media(Base):
    """An image stored in ImageKit and owned by one user."""

    __tablename__ = "media"
    __table_args__ = (Index("ix_media_status_created_at", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    imagekit_file_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status", values_callable=_status_values),
        nullable=False,
        default=MediaStatus.pending,
        server_default=MediaStatus.pending.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=func.now(),
    )
