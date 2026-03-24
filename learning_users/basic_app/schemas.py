"""Pydantic schemas for JSON API bodies (wire format camelCase)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KanbanColumnReorderBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    column_keys: list[str] = Field(alias="columnKeys")
