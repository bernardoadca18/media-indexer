from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile

from app.api.dependencies import DB, StorageDep
from app.models import Note
from app.repositories.domain import ProjectRepository, SessionRepository
from app.schemas.domain import (
    NoteOut,
    ProjectCreate,
    ProjectOut,
    SessionIngestOut,
    SessionOut,
    SessionSummary,
    StructuredNoteIn,
)
from app.services.ingestion import IngestionService, InvalidNotesError, ProjectNotFoundError
from app.workers.runner import job_runner

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate, db: DB) -> object:
    item = await ProjectRepository(db).create(payload.name, payload.description)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/{project_id}", response_model=ProjectOut)
async def ensure_project(project_id: UUID, payload: ProjectCreate, db: DB) -> object:
    """Create-only idempotent logical project identity for external production owners."""
    from sqlalchemy.dialects.postgresql import insert
    from app.models import MediaProject

    await db.execute(insert(MediaProject).values(
        id=project_id, name=payload.name, description=payload.description,
    ).on_conflict_do_nothing(index_elements=[MediaProject.id]))
    await db.commit()
    item = await ProjectRepository(db).get(project_id)
    if item.name != payload.name or item.description != payload.description:
        raise HTTPException(409, "Project identity already belongs to different metadata")
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
    notes: Annotated[UploadFile | None, File()] = None,
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


@router.post(
    "/{project_id}/sessions/{session_id}/notes", response_model=SessionOut, status_code=200
)
async def import_session_notes(
    project_id: UUID,
    session_id: UUID,
    db: DB,
    notes: Annotated[UploadFile, File()],
) -> object:
    """Import notes into an existing session (video-only ingest that now has notes)."""
    from app.services.note_parser import split_note_units

    session = await SessionRepository(db).get(session_id)
    if session is None or session.project_id != project_id:
        raise HTTPException(404, "Session not found")
    if session.raw_notes is not None:
        raise HTTPException(409, "Session already has notes")

    raw_bytes = await notes.read()
    if not raw_bytes.strip():
        raise HTTPException(400, "notes file cannot be empty")
    try:
        raw_notes = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "notes must be UTF-8") from exc

    session.raw_notes = raw_notes
    session.notes_filename = notes.filename or "notes.txt"
    for order, raw in enumerate(split_note_units(raw_notes), start=1):
        db.add(Note(session_id=session_id, order=order, raw=raw))
    await db.commit()
    await db.refresh(session)
    return session


@router.patch(
    "/{project_id}/sessions/{session_id}/notes/{note_id}",
    response_model=NoteOut,
    status_code=200,
)
async def update_structured_note(
    project_id: UUID,
    session_id: UUID,
    note_id: UUID,
    db: DB,
    payload: StructuredNoteIn,
) -> object:
    """Update derived temporal fields on a note (preserves raw text)."""
    from sqlalchemy import select

    stmt = select(Note).where(
        Note.id == note_id,
        Note.session_id == session_id,
    )
    result = await db.execute(stmt)
    note = result.scalar_one_or_none()

    if note is None:
        raise HTTPException(404, "Note not found")

    # Verify session belongs to project
    session = await SessionRepository(db).get(session_id)
    if session is None or session.project_id != project_id:
        raise HTTPException(404, "Session not found")

    # Only update derived temporal fields, raw is immutable via trigger
    note.start_seconds = payload.start_seconds
    note.end_seconds = payload.end_seconds
    note.description = payload.description
    note.tags = payload.tags

    await db.commit()
    await db.refresh(note)
    return note
