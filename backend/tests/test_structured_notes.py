"""Tests for structured timeline notes API."""
from uuid import UUID, uuid4

from httpx import AsyncClient

from app.repositories.domain import ProjectRepository, SessionRepository


async def _create_session_with_notes(client: AsyncClient, db) -> tuple[UUID, UUID]:
    """Helper to create a project, session with notes, return (project_id, session_id)."""
    project = await ProjectRepository(db).create("Test Project", "For testing")
    await db.commit()

    # Create session via ingest
    from io import BytesIO

    video_content = b"fake video content for testing"
    notes_content = b"First note\n---\nSecond note\n---\nThird note"

    files = {
        "video": ("test.mp4", BytesIO(video_content), "video/mp4"),
        "notes": ("notes.txt", BytesIO(notes_content), "text/plain"),
    }
    data = {"label": "Test Session"}

    resp = await client.post(f"/projects/{project.id}/sessions", files=files, data=data)
    assert resp.status_code == 202
    result = resp.json()
    return project.id, UUID(result["session"]["id"])


async def test_update_structured_note_temporal_fields(client: AsyncClient, db) -> None:
    """Test updating derived temporal fields on a note."""
    project_id, session_id = await _create_session_with_notes(client, db)

    # Get notes for the session
    session = await SessionRepository(db).get(session_id)
    notes = list(session.notes)
    assert len(notes) >= 1  # At least one note created

    note_id = notes[0].id

    # Update temporal fields
    update_payload = {
        "order": 1,
        "raw": notes[0].raw,
        "start_seconds": 0.0,
        "end_seconds": 5.5,
        "description": "Opening scene",
        "tags": ["intro", "action"],
    }

    resp = await client.patch(
        f"/projects/{project_id}/sessions/{session_id}/notes/{note_id}",
        json=update_payload,
    )
    assert resp.status_code == 200
    result = resp.json()

    assert result["start_seconds"] == 0.0
    assert result["end_seconds"] == 5.5
    assert result["description"] == "Opening scene"
    assert result["tags"] == ["intro", "action"]
    assert result["raw"] == notes[0].raw  # raw preserved


async def test_update_structured_note_validates_range(client: AsyncClient, db) -> None:
    """Test that end_seconds must be >= start_seconds."""
    project_id, session_id = await _create_session_with_notes(client, db)

    session = await SessionRepository(db).get(session_id)
    notes = list(session.notes)
    note_id = notes[0].id

    # Invalid: end < start
    update_payload = {
        "order": 1,
        "raw": notes[0].raw,
        "start_seconds": 10.0,
        "end_seconds": 5.0,  # Invalid: less than start
    }

    resp = await client.patch(
        f"/projects/{project_id}/sessions/{session_id}/notes/{note_id}",
        json=update_payload,
    )
    assert resp.status_code == 422  # Validation error


async def test_update_structured_note_validates_negative_start(client: AsyncClient, db) -> None:
    """Test that start_seconds must be >= 0."""
    project_id, session_id = await _create_session_with_notes(client, db)

    session = await SessionRepository(db).get(session_id)
    notes = list(session.notes)
    note_id = notes[0].id

    # Invalid: negative start
    update_payload = {
        "order": 1,
        "raw": notes[0].raw,
        "start_seconds": -5.0,  # Invalid: negative
    }

    resp = await client.patch(
        f"/projects/{project_id}/sessions/{session_id}/notes/{note_id}",
        json=update_payload,
    )
    assert resp.status_code == 422  # Validation error


async def test_update_structured_note_not_found(client: AsyncClient, db) -> None:
    """Test 404 when updating non-existent note."""
    project_id, session_id = await _create_session_with_notes(client, db)

    fake_note_id = uuid4()
    update_payload = {
        "order": 1,
        "raw": "some text",
        "start_seconds": 0.0,
    }

    resp = await client.patch(
        f"/projects/{project_id}/sessions/{session_id}/notes/{fake_note_id}",
        json=update_payload,
    )
    assert resp.status_code == 404


async def test_note_out_includes_structured_fields(client: AsyncClient, db) -> None:
    """Test that NoteOut includes structured temporal fields."""
    project_id, session_id = await _create_session_with_notes(client, db)

    session = await SessionRepository(db).get(session_id)
    notes = list(session.notes)
    note_id = notes[1].id

    # Update with structured data
    update_payload = {
        "order": 2,
        "raw": notes[1].raw,
        "start_seconds": 10.5,
        "end_seconds": 25.0,
        "description": "Middle sequence",
        "tags": ["dialogue"],
    }

    resp = await client.patch(
        f"/projects/{project_id}/sessions/{session_id}/notes/{note_id}",
        json=update_payload,
    )
    assert resp.status_code == 200

    # Verify response includes all structured fields
    result = resp.json()
    assert "start_seconds" in result
    assert "end_seconds" in result
    assert "description" in result
    assert "tags" in result
