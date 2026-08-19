from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB
from app.schemas.semantic_timeline import SemanticTimelineOut
from app.services.semantic_timeline import (
    SemanticTimelinePrerequisiteError,
    SemanticTimelineService,
)

router = APIRouter(tags=["semantic-timeline"])


@router.get(
    "/sessions/{session_id}/semantic-timeline",
    response_model=SemanticTimelineOut,
)
async def get_semantic_timeline(
    session_id: UUID,
    db: DB,
    temporal_index_id: Annotated[UUID, Query()],
    alignment_run_id: Annotated[UUID | None, Query()] = None,
    start_seconds: Annotated[float | None, Query(ge=0)] = None,
    end_seconds: Annotated[float | None, Query(ge=0)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SemanticTimelineOut:
    try:
        return await SemanticTimelineService(db).get(
            session_id=session_id,
            temporal_index_id=temporal_index_id,
            alignment_run_id=alignment_run_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            offset=offset,
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SemanticTimelinePrerequisiteError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
