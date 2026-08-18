from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB
from app.core.config import settings
from app.models import MediaSession
from app.repositories.visual_index import VisualIndexRepository
from app.schemas.visual_index import (
    DerivedAssetPage,
    SceneSegmentPage,
    VisualIndexCreate,
    VisualIndexJobOut,
    VisualIndexOut,
)
from app.services.visual_index import (
    MediaDurationUnavailableError,
    VisualIndexAttemptsExhaustedError,
    VisualIndexRequest,
    VisualIndexService,
    VisualSessionNotReadyError,
)
from app.workers.runner import job_runner

router = APIRouter(tags=["visual-indexes"])


@router.post(
    "/sessions/{session_id}/visual-indexes",
    response_model=VisualIndexJobOut,
    status_code=202,
)
async def create_visual_index(
    session_id: UUID, payload: VisualIndexCreate, db: DB
) -> VisualIndexJobOut:
    request = VisualIndexRequest(
        scene_detection_enabled=(
            settings.scene_detection_enabled
            if payload.scene_detection_enabled is None
            else payload.scene_detection_enabled
        ),
        scene_detector=payload.scene_detector or settings.scene_detector,
        scene_threshold=(
            settings.scene_threshold if payload.scene_threshold is None else payload.scene_threshold
        ),
        periodic_frame_interval_seconds=(
            settings.representative_frame_interval_seconds
            if payload.periodic_frame_interval_seconds is None
            else payload.periodic_frame_interval_seconds
        ),
        frame_width=payload.frame_width or settings.representative_frame_width,
        image_format=payload.image_format or settings.representative_frame_format,
        jpeg_quality=(
            settings.representative_frame_jpeg_quality
            if payload.jpeg_quality is None
            else payload.jpeg_quality
        ),
        dedup_tolerance_seconds=(
            settings.representative_frame_dedup_tolerance_seconds
            if payload.dedup_tolerance_seconds is None
            else payload.dedup_tolerance_seconds
        ),
    )
    try:
        result = await VisualIndexService(db, settings.job_max_attempts).ensure(session_id, request)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        VisualSessionNotReadyError,
        MediaDurationUnavailableError,
        VisualIndexAttemptsExhaustedError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return VisualIndexJobOut(
        visual_index=result.visual_index,
        job=result.job,
        created=result.created,
    )


@router.get(
    "/sessions/{session_id}/visual-indexes",
    response_model=list[VisualIndexOut],
)
async def list_visual_indexes(session_id: UUID, db: DB) -> object:
    if await db.get(MediaSession, session_id) is None:
        raise HTTPException(404, "session not found")
    return await VisualIndexRepository(db).list_for_session(session_id)


@router.get("/visual-indexes/{visual_index_id}", response_model=VisualIndexOut)
async def get_visual_index(visual_index_id: UUID, db: DB) -> object:
    item = await VisualIndexRepository(db).get(visual_index_id)
    if item is None:
        raise HTTPException(404, "visual index not found")
    return item


@router.get(
    "/visual-indexes/{visual_index_id}/scenes",
    response_model=SceneSegmentPage,
)
async def list_scenes(
    visual_index_id: UUID,
    db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SceneSegmentPage:
    repository = VisualIndexRepository(db)
    if await repository.get(visual_index_id) is None:
        raise HTTPException(404, "visual index not found")
    items, total = await repository.scenes(visual_index_id, offset, limit)
    return SceneSegmentPage(items=list(items), total=total, offset=offset, limit=limit)


@router.get(
    "/visual-indexes/{visual_index_id}/frames",
    response_model=DerivedAssetPage,
)
async def list_frames(
    visual_index_id: UUID,
    db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DerivedAssetPage:
    repository = VisualIndexRepository(db)
    if await repository.get(visual_index_id) is None:
        raise HTTPException(404, "visual index not found")
    items, total = await repository.frames(visual_index_id, offset, limit)
    return DerivedAssetPage(items=list(items), total=total, offset=offset, limit=limit)
