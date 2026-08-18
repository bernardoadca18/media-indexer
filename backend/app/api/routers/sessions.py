from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.dependencies import DB
from app.repositories.domain import SessionRepository
from app.schemas.domain import SessionOut

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: UUID, db: DB) -> object:
    item = await SessionRepository(db).get(session_id)
    if item is None:
        raise HTTPException(404, "Session not found")
    return item
