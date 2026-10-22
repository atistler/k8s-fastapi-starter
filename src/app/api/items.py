"""Sample CRUD resource under /api/v1/items with Redis cache-aside on GET by id.

Cache-aside has a known, accepted limitation: a reader that misses the cache and
then populates it can race a concurrent update/delete that already invalidated
the key, leaving a stale entry until the TTL (CACHE_TTL_SECONDS) expires. The
short TTL bounds that window. If strict freshness is ever required, switch to
write-through or add versioned keys; for this template the simple pattern is the
point.
"""

from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.cache import RedisDep
from app.db import SessionDep
from app.models import Item
from app.schemas import ItemCreate, ItemRead, ItemUpdate

router = APIRouter(prefix="/api/v1/items", tags=["items"])

CACHE_TTL_SECONDS: Final = 300


def _cache_key(item_id: int) -> str:
    return f"items:{item_id}"


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_item(payload: ItemCreate, session: SessionDep) -> ItemRead:
    item = Item(name=payload.name)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return ItemRead.model_validate(item)


@router.get("")
async def list_items(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ItemRead]:
    result = await session.execute(
        select(Item).order_by(Item.id).limit(limit).offset(offset)
    )
    return [ItemRead.model_validate(row) for row in result.scalars().all()]


@router.get("/{item_id}")
async def get_item(item_id: int, session: SessionDep, redis: RedisDep) -> ItemRead:
    """Cache-aside: check Redis first, then Postgres, then populate the cache."""
    key = _cache_key(item_id)
    cached = await redis.get(key)
    if cached is not None:
        return ItemRead.model_validate_json(cached)

    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="item not found"
        )
    result = ItemRead.model_validate(item)
    await redis.set(key, result.model_dump_json(), ex=CACHE_TTL_SECONDS)
    return result


@router.put("/{item_id}")
async def replace_item(
    item_id: int,
    payload: ItemUpdate,
    session: SessionDep,
    redis: RedisDep,
) -> ItemRead:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="item not found"
        )
    item.name = payload.name
    await session.commit()
    await session.refresh(item)
    await redis.delete(_cache_key(item_id))
    return ItemRead.model_validate(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: int, session: SessionDep, redis: RedisDep) -> Response:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="item not found"
        )
    await session.delete(item)
    await session.commit()
    await redis.delete(_cache_key(item_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
