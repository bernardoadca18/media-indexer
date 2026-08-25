from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.dependencies import DB
from app.api.routers import (
    alignments,
    candidate_retrieval,
    jobs,
    media_assets,
    media_search,
    projects,
    semantic_timeline,
    sessions,
    temporal_indexes,
    transcriptions,
    visual_indexes,
)
from app.core.config import settings
from app.core.logging import configure_logging
from app.workers.runner import job_runner

configure_logging(settings.log_level)


def create_app(start_worker: bool | None = None) -> FastAPI:
    enabled = settings.worker_enabled if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if enabled:
            await job_runner.start()
        yield
        if enabled:
            await job_runner.stop()

    application = FastAPI(title="NeoVids Media Indexer", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[item.strip() for item in settings.cors_origins.split(",")],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(projects.router)
    application.include_router(transcriptions.router)
    application.include_router(sessions.router)
    application.include_router(semantic_timeline.router)
    application.include_router(visual_indexes.router)
    application.include_router(temporal_indexes.router)
    application.include_router(candidate_retrieval.router)
    application.include_router(alignments.router)
    application.include_router(jobs.router)
    application.include_router(media_assets.router)
    application.include_router(media_search.router)

    @application.get("/health", tags=["health"])
    async def health(db: DB) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return application


app = create_app()
