from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.dependencies import DB
from app.repositories.domain import JobRepository
from app.schemas.domain import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, db: DB) -> object:
    item = await JobRepository(db).get(job_id)
    if item is None:
        raise HTTPException(404, "Job not found")
    return item
