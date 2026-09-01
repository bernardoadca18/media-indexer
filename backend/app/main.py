from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
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
from app.services.model_gateway import model_gateway
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
        await model_gateway.close()

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

    @application.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    async def readiness(db: DB) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        if enabled and not job_runner.operational:
            raise HTTPException(503, "job worker is not operational")
        return {"status": "ok"}

    application.get("/health/ready", tags=["health"])(readiness)
    application.get("/health", tags=["health"])(readiness)

    return application


app = create_app()
