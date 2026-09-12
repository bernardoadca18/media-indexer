from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.dependencies import DB, EmbeddingDep
from app.schemas.image import ImageSearchRequest, ImageSearchResponse
from app.services.embedding_provider import EmbeddingDimensionError, EmbeddingProviderError
from app.services.image_search import ImageSearchError, ImageSearchService

router = APIRouter(prefix="/image-search", tags=["image-search"])


@router.post("/projects/{project_id}", response_model=ImageSearchResponse)
async def search_project_images(
    project_id: UUID,
    payload: ImageSearchRequest,
    db: DB,
    embedding_provider: EmbeddingDep,
) -> ImageSearchResponse:
    try:
        return await ImageSearchService(db, embedding_provider).search_project(project_id, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ImageSearchError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (EmbeddingProviderError, EmbeddingDimensionError) as exc:
        raise HTTPException(502, str(exc)) from exc
