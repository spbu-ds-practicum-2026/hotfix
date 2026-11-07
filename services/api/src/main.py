from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from storage.shard_router.router import ShardRouter

from api.error_handlers import register_error_handlers
from api.submissions import get_shard_router
from api.submissions import router as submissions_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    shard_router = ShardRouter.from_environment()
    application.dependency_overrides[get_shard_router] = lambda: shard_router
    yield
    await shard_router.dispose()


app = FastAPI(title="VERDICT API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(submissions_router)