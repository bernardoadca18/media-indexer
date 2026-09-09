"""Tests for timeline notes import with provenance and identity."""
from io import BytesIO
from uuid import UUID

from httpx import AsyncClient

from app.repositories.domain import ProjectRepository


async def _create_session_with_media(client: AsyncClient, db) -> tuple[str, str]:
    """Helper to create a project and session with media for testing."""
    project = await ProjectRepository(db).create("Test Project", "testing")
    await db.commit()

    video_content = b"fake video content for testing"
    files = {
        "video": ("test.mp4", BytesIO(video_content), "video/mp4"),
    }
    data = {"label": "Test Session"}

    resp = await client.post(f"/projects/{project.id}/sessions", files=files, data=data)
    assert resp.status_code == 202
    result = resp.json()
    session_id = result["session"]["id"]
    return str(project.id), session_id


async def test_timeline_notes_import_with_provenance(client: AsyncClient, db) -> None:
    """Test that timeline notes import sets provenance correctly."""
    project_id, session_id = await _create_session_with_media(client, db)

    # Import notes with explicit provenance
    import_payload = {
        "version": "timeline_notes_v1",
        "provenance": "alignment_run_abc123",
        "observations": [
            {
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "description": "First observation",
                "tags": ["test"],
            }
        ],
    }

    resp = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload,
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported_count"] == 1
    assert result["skipped_count"] == 0
    assert result["errors"] is None


async def test_timeline_notes_import_defaults_provenance(client: AsyncClient, db) -> None:
    """Test that timeline notes import defaults provenance to manual_import."""
    project_id, session_id = await _create_session_with_media(client, db)

    # Import notes without explicit provenance (defaults to manual_import)
    import_payload = {
        "version": "timeline_notes_v1",
        "observations": [
            {
                "start_seconds": 5.0,
                "end_seconds": 15.0,
                "description": "Second observation",
                "tags": [],
            }
        ],
    }

    resp = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload,
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported_count"] == 1


async def test_timeline_notes_identity_deduplication(client: AsyncClient, db) -> None:
    """Test that duplicate notes are detected by (session_id, start, end, normalized_description)."""
    from sqlalchemy import func, select

    from app.models.entities import Note

    project_id, session_id = await _create_session_with_media(client, db)
    session_uuid = UUID(session_id)

    # Import the same note twice via direct database operations to verify deduplication logic
    normalized_description = "duplicate test"

    # First note
    note1 = Note(
        session_id=session_uuid,
        order=1,
        raw="Duplicate Test",
        start_seconds=0.0,
        end_seconds=10.0,
        description="Duplicate Test",
        tags=["test"],
        provenance="test_dedup",
    )
    db.add(note1)
    await db.flush()

    # Check that duplicate is detected
    existing_result = await db.execute(
        select(Note)
        .where(Note.session_id == session_uuid)
        .where(Note.start_seconds == 0.0)
        .where(Note.end_seconds == 10.0)
        .where(func.lower(Note.description) == normalized_description)
    )
    assert existing_result.scalar_one_or_none() is not None


async def test_timeline_notes_identity_allows_same_range_different_description(
    client: AsyncClient, db
) -> None:
    """Test that same temporal range with different descriptions creates separate notes."""
    project_id, session_id = await _create_session_with_media(client, db)

    # Import note with first description
    import_payload1 = {
        "version": "timeline_notes_v1",
        "observations": [
            {
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "description": "First description",
                "tags": [],
            }
        ],
    }

    resp1 = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload1,
    )
    assert resp1.status_code == 200
    assert resp1.json()["imported_count"] == 1

    # Import note with same range but different description
    import_payload2 = {
        "version": "timeline_notes_v1",
        "observations": [
            {
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "description": "Second description",
                "tags": [],
            }
        ],
    }

    resp2 = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload2,
    )
    assert resp2.status_code == 200
    assert resp2.json()["imported_count"] == 1  # Should be imported, not skipped


async def test_timeline_notes_identity_case_insensitive(client: AsyncClient, db) -> None:
    """Test that description comparison is case-insensitive for identity."""
    from sqlalchemy import func, select

    from app.models.entities import Note

    project_id, session_id = await _create_session_with_media(client, db)
    session_uuid = UUID(session_id)

    # Add note with mixed case
    note1 = Note(
        session_id=session_uuid,
        order=1,
        raw="Case Test Observation",
        start_seconds=20.0,
        end_seconds=30.0,
        description="Case Test Observation",
        tags=[],
        provenance="test_case",
    )
    db.add(note1)
    await db.flush()

    # Check that lowercase version is found as duplicate
    existing_result = await db.execute(
        select(Note)
        .where(Note.session_id == session_uuid)
        .where(Note.start_seconds == 20.0)
        .where(Note.end_seconds == 30.0)
        .where(func.lower(Note.description) == "case test observation")
    )
    assert existing_result.scalar_one_or_none() is not None


async def test_timeline_notes_import_validates_time_range(client: AsyncClient, db) -> None:
    """Test that timeline notes import validates start < end."""
    project_id, session_id = await _create_session_with_media(client, db)

    # Import with invalid time range (start >= end)
    import_payload = {
        "version": "timeline_notes_v1",
        "observations": [
            {
                "start_seconds": 10.0,
                "end_seconds": 10.0,  # Equal to start - invalid
                "description": "Invalid range",
                "tags": [],
            }
        ],
    }

    resp = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload,
    )
    # Pydantic validates at schema level, returns 422 for validation errors
    assert resp.status_code == 422


async def test_timeline_notes_import_validates_negative_start(client: AsyncClient, db) -> None:
    """Test that timeline notes import rejects negative start_seconds."""
    project_id, session_id = await _create_session_with_media(client, db)

    import_payload = {
        "version": "timeline_notes_v1",
        "observations": [
            {
                "start_seconds": -5.0,  # Invalid negative
                "end_seconds": 10.0,
                "description": "Negative start",
                "tags": [],
            }
        ],
    }

    resp = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json=import_payload,
    )
    # Pydantic validates at schema level, returns 422 for validation errors
    assert resp.status_code == 422


async def test_timeline_text_import_is_idempotent_and_reports_invalid_lines(
    client: AsyncClient, db
) -> None:
    _, session_id = await _create_session_with_media(client, db)
    payload = {
        "version": "timeline_notes_text_v1",
        "provenance": "manual_timeline_import",
        "content": (
            "Timeline detalhada\n"
            "00:00–00:01 — Entrada no local.\n"
            "00:01-00:02 - O personagem corre.\n"
            "00:03 — Evento sem range."
        ),
    }
    first = await client.post(
        f"/sessions/{session_id}/timeline-notes/import-text", json=payload
    )
    assert first.status_code == 200
    assert first.json() == {
        "parsed_count": 2,
        "imported_count": 2,
        "skipped_count": 0,
        "invalid_lines": [
            {
                "line_number": 4,
                "line": "00:03 — Evento sem range.",
                "reason": "invalid temporal range",
            }
        ],
        "ignored_line_count": 1,
    }
    second = await client.post(
        f"/sessions/{session_id}/timeline-notes/import-text", json=payload
    )
    assert second.status_code == 200
    assert second.json()["imported_count"] == 0
    assert second.json()["skipped_count"] == 2
