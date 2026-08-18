from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile

from app.api.dependencies import DB, StorageDep
from app.repositories.domain import ProjectRepository, SessionRepository
from app.schemas.domain import ProjectCreate, ProjectOut, SessionIngestOut, SessionSummary
from app.services.ingestion import IngestionService, InvalidNotesError, ProjectNotFoundError
from app.workers.runner import job_runner

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate, db: DB) -> object:
    item = await ProjectRepository(db).create(payload.name, payload.description)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    db: DB, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> object:
    return await ProjectRepository(db).list(offset, limit)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID, db: DB) -> object:
    item = await ProjectRepository(db).get(project_id)
    if item is None:
        raise HTTPException(404, "Project not found")
    return item


@router.get("/{project_id}/sessions", response_model=list[SessionSummary])
async def list_sessions(project_id: UUID, db: DB) -> object:
    if await ProjectRepository(db).get(project_id) is None:
        raise HTTPException(404, "Project not found")
    return await SessionRepository(db).list_for_project(project_id)


@router.post("/{project_id}/sessions", response_model=SessionIngestOut, status_code=202)
async def ingest_session(
    project_id: UUID,
    db: DB,
    storage: StorageDep,
    label: Annotated[str, Form(min_length=1, max_length=200)],
    video: Annotated[UploadFile, File()],
    notes: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> object:
    try:
        result = await IngestionService(db, storage).ingest(
            project_id, label, video, notes, idempotency_key
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    except InvalidNotesError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result.created:
        job_runner.enqueue(result.job.id)
    return SessionIngestOut(session=result.session, job=result.job)
