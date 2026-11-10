from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from domain.repository import ShardedDomainRepository, SqlDomainRepository
from fastapi import FastAPI
from sqlalchemy import create_engine
from storage.shard_router.router import ShardRouter

from api.contests import get_domain_repository
from api.contests import router as contests_router
from api.error_handlers import register_error_handlers
from api.submissions import get_shard_router
from api.submissions import router as submissions_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    shard_router = ShardRouter.from_environment()
    sync_engines = {
        shard_id: create_engine(
            shard.url.set(drivername="postgresql+psycopg"), pool_pre_ping=True
        )
        for shard_id, shard in shard_router.settings.shards.items()
    }
    domain_repository = ShardedDomainRepository(
        {shard_id: SqlDomainRepository(engine) for shard_id, engine in sync_engines.items()},
        shard_selector=shard_router.shard_for_contest,
    )
    application.dependency_overrides[get_shard_router] = lambda: shard_router
    application.dependency_overrides[get_domain_repository] = lambda: domain_repository
    yield
    await shard_router.dispose()
    for engine in sync_engines.values():
        engine.dispose()


app = FastAPI(title="VERDICT API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(contests_router)
app.include_router(submissions_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
