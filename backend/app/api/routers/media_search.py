"""Media Search API endpoints.

Provides session-level and project-level media search endpoints
that hide internal pipeline details from consumers.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_embedding_provider
from app.schemas.media_search import MediaSearchRequest, MediaSearchResponse
from app.services.embedding_provider import EmbeddingProvider
from app.services.media_search import MediaSearchError, MediaSearchService

router = APIRouter(prefix="/media-search", tags=["media-search"])


@router.post(
    "/sessions/{session_id}",
    response_model=MediaSearchResponse,
    summary="Search media within a session",
    description="""
Search for footage within a single session.

The query is embedded once and compared against the session's temporal index
to find the most relevant footage ranges.

Results include:
- Session, media, and temporal coordinates
- Relevance score and global rank
- Transcript preview
- Provenance for debugging

This endpoint does NOT run multimodal alignment by default.
It performs deterministic vector similarity search only.
""",
    responses={
        200: {"description": "Search completed (may have zero results)"},
        404: {"description": "Session not found"},
        409: {"description": "No suitable temporal index available"},
        422: {"description": "Invalid query or parameters"},
        502: {"description": "Embedding provider failure"},
    },
)
async def search_session(
    session_id: UUID,
    request: MediaSearchRequest,
    db: AsyncSession = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> MediaSearchResponse:
    service = MediaSearchService(db, embedding_provider)

    try:
        return await service.search_session(session_id, request)
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except MediaSearchError as e:
        # Distinguish between different error types
        error_message = str(e).lower()

        if "not found" in error_message or "no completed" in error_message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.post(
    "/projects/{project_id}",
    response_model=MediaSearchResponse,
    summary="Search media across a project",
    description="""
Search for footage across multiple sessions in a project.

This endpoint:
1. Lists all eligible sessions in the project
2. Resolves a temporal index for each session
3. Groups sessions by embedding compatibility
4. Embeds the query ONCE per compatibility group
5. Performs retrieval on each compatible index
6. Aggregates and globally ranks results

Results are deduplicated within the same session and ranked globally by score.

This endpoint does NOT run multimodal alignment by default.
It performs deterministic vector similarity search only.
""",
    responses={
        200: {"description": "Search completed (may have zero results)"},
        404: {"description": "Project not found"},
        409: {"description": "No suitable temporal indexes available"},
        422: {"description": "Invalid query or parameters"},
        502: {"description": "Embedding provider failure"},
    },
)
async def search_project(
    project_id: UUID,
    request: MediaSearchRequest,
    db: AsyncSession = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> MediaSearchResponse:
    service = MediaSearchService(db, embedding_provider)

    try:
        return await service.search_project(project_id, request)
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except MediaSearchError as e:
        error_message = str(e).lower()

        if "not found" in error_message or "no sessions" in error_message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
