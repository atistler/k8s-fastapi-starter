"""Pydantic request and response model validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import Item
from app.schemas import ItemCreate, ItemRead, ItemUpdate


def test_item_create_accepts_valid_name() -> None:
    assert ItemCreate(name="widget").name == "widget"


@pytest.mark.parametrize("name", ["", "x" * 201])
def test_item_create_rejects_out_of_range_name(name: str) -> None:
    with pytest.raises(ValidationError):
        ItemCreate(name=name)


def test_item_create_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ItemCreate.model_validate({"name": "widget", "id": 99})


def test_item_update_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        ItemUpdate(name="")


def test_item_read_builds_from_orm_model() -> None:
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    item = Item(id=7, name="widget", created_at=created)

    read = ItemRead.model_validate(item)

    assert read.id == 7
    assert read.name == "widget"
    assert read.created_at == created
