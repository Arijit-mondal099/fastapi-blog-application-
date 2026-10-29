"""Request and response schemas for media and upload authentication."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.media import MediaStatus


class MediaConfirmRequest(BaseModel):
    """Fields accepted when confirming an ImageKit upload."""

    imagekit_file_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    mime_type: str
    size_bytes: int


class MediaResponse(BaseModel):
    """A media record as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    imagekit_file_id: str
    url: str
    mime_type: str
    size_bytes: int
    status: MediaStatus
    created_at: datetime
    updated_at: datetime


class UploadAuthResponse(BaseModel):
    """Short-lived parameters the browser sends directly to ImageKit."""

    token: str
    expire: int
    signature: str
    public_key: str
    folder: str
    file_name: str
