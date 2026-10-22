"""Pydantic request and response models. These are the only API payload types."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    """Body for ``POST /api/v1/items``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ItemUpdate(BaseModel):
    """Body for ``PUT /api/v1/items/{id}`` (full replacement)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ItemRead(BaseModel):
    """Item as returned by the API. Built from the ORM model."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
