from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import DB, StorageDep
from app.core.config import settings
from app.repositories.domain import TemporalDerivativeRepository
from app.schemas.temporal_derivative import (
    TemporalDerivativeEnsureOut,
    TemporalDerivativeEnsureRequest,
    TemporalDerivativeOut,
)
from app.services.temporal_derivative import (
    TemporalDerivativeAttemptsExhaustedError,
    TemporalDerivativeConflictError,
    TemporalDerivativeService,
)
from app.workers.runner import job_runner

router = APIRouter(tags=["temporal-derivatives"])


def _out(item: object) -> TemporalDerivativeOut:
    derivative = TemporalDerivativeOut.model_validate(item)
    content_url = (
        f"/temporal-derivatives/{derivative.id}/content"
        if derivative.status == "completed"
        else None
    )
    return derivative.model_copy(update={"content_url": content_url})


@router.post(
    "/media-assets/{media_id}/temporal-derivatives",
    response_model=TemporalDerivativeEnsureOut,
    status_code=202,
)
async def ensure_temporal_derivative(
    media_id: UUID,
    payload: TemporalDerivativeEnsureRequest,
    db: DB,
    storage: StorageDep,
) -> TemporalDerivativeEnsureOut:
    try:
        result = await TemporalDerivativeService(db, storage, settings.job_max_attempts).ensure(
            media_id, payload
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (TemporalDerivativeConflictError, TemporalDerivativeAttemptsExhaustedError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return TemporalDerivativeEnsureOut(
        derivative=_out(result.derivative),
        job=result.job,
        created=result.created,
    )


@router.get(
    "/temporal-derivatives/{derivative_id}",
    response_model=TemporalDerivativeOut,
)
async def get_temporal_derivative(derivative_id: UUID, db: DB) -> TemporalDerivativeOut:
    item = await TemporalDerivativeRepository(db).get(derivative_id)
    if item is None:
        raise HTTPException(404, "temporal derivative not found")
    return _out(item)


@router.get("/temporal-derivatives/{derivative_id}/content")
async def get_temporal_derivative_content(
    derivative_id: UUID, db: DB, storage: StorageDep
) -> FileResponse:
    item = await TemporalDerivativeRepository(db).get(derivative_id)
    if item is None:
        raise HTTPException(404, "temporal derivative not found")
    if item.status != "completed" or item.storage_uri is None or item.sha256 is None:
        raise HTTPException(409, "temporal derivative is not completed")
    try:
        path = storage.resolve_any(item.storage_uri)
    except ValueError as exc:
        raise HTTPException(404, "temporal derivative content unavailable") from exc
    if not path.is_file():
        raise HTTPException(404, "temporal derivative content unavailable")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{item.identity}.mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, immutable, max-age=31536000",
            "ETag": f'"{item.sha256}"',
            "X-Content-SHA256": item.sha256,
        },
    )
