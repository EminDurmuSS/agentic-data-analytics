"""Validated request bodies for the local workspace API."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceBody(StrictBody):
    name: str = Field(default="Yeni analiz", min_length=1, max_length=100)
    profile: str = Field(default="finance", pattern="^(finance|generic)$")


class RunBody(StrictBody):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)


class SourceBody(StrictBody):
    url: str = Field(min_length=8, max_length=2000)


class ReviewBody(StrictBody):
    table_id: str = Field(min_length=1, max_length=160)
    reviewed_rows: list = Field(min_length=1, max_length=50000)
    unit_evidence: dict[str, str]
