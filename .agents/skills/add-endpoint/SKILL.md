---
name: add-endpoint
description: Use when adding a new FastAPI route or resource (a new API endpoint, CRUD resource, or router) to this k8s-fastapi-starter service — covers schemas, router wiring, optional DB model/migration, caching, tests, and the quality gates.
---

# Add a FastAPI endpoint

Follow these steps for a new route or resource. Read `SPEC.md` §4 (application
design) first.

## Steps

1. **Schema** — add Pydantic request/response models in
   `src/app/schemas.py`. These are the *only* API payload types. Mirror the
   existing `Item*` models: `model_config = ConfigDict(extra="forbid")` on input
   bodies, `ConfigDict(from_attributes=True)` on read models built from ORM
   objects, and `Field(...)` constraints for validation.

2. **Route module** — add a new module under `src/app/api/` (e.g.
   `src/app/api/<resource>.py`). Define a module-level
   `router = APIRouter(prefix="/api/v1/<resource>", tags=["<resource>"])`.
   Inject dependencies with the typed aliases used in `api/items.py`:
   `SessionDep` from `app.db` and `RedisDep` from `app.cache`. Every handler is
   `async` and fully annotated.

3. **Register the router** — in `src/app/main.py`, import your router and add
   `app.include_router(<resource>_router)` inside `create_app`, alongside the
   existing `health_router` and `items_router`.

4. **Storage (if needed)** — if the resource persists data, add a SQLAlchemy
   model in `src/app/models.py` using `Mapped[...]` + `mapped_column(...)` (see
   the `Item` model), then create a migration. **Use the `add-migration`
   skill** — do not hand-write SQL or edit the DB directly.

5. **Caching (if needed)** — for read-heavy GET-by-id endpoints, reuse the
   Redis cache-aside pattern from `src/app/api/items.py` (`get_item`): check
   `redis.get(key)` first, fall back to Postgres, then `redis.set(..., ex=TTL)`;
   invalidate with `redis.delete(key)` on update/delete.

6. **Tests** — add unit tests under `tests/unit/` (must run with no services,
   via httpx `ASGITransport` against the app). If the endpoint hits Postgres or
   Redis, add tests under `tests/integration/` marked
   `@pytest.mark.integration`. Run `uv run poe up` first for integration tests.

7. **Gates** — run `uv run poe check`. Keep `mypy --strict` clean (no
   blanket `# type: ignore`; justify any `Any`). Update `docs/` if the change is
   user-visible (`mkdocs build --strict` must pass).

8. **Commit** — Conventional Commit, e.g. `feat(<resource>): add create and
   list endpoints`.

See `AGENTS.md` for the full rules and `SPEC.md` for the source of truth.
