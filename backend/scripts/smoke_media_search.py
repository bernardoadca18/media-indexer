#!/usr/bin/env python3
"""Smoke test for Media Search functionality.

This script creates deterministic test data and validates:
- Session search returns ranked results
- Project search aggregates multiple sessions
- Query is embedded only once per compatibility group
- Cross-session ranking works correctly
- Zero results is handled properly

Run from backend directory:
    python scripts/smoke_media_search.py

Environment variables:
    DATABASE_URL: PostgreSQL async connection string
"""

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    MediaAsset,
    MediaProject,
    MediaSession,
    Note,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.schemas.media_search import MediaSearchRequest
from app.services.embedding_provider import FakeEmbeddingProvider
from app.services.media_search import MediaSearchService


def log(message: str) -> None:
    """Print timestamped log message."""
    timestamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}")


async def create_test_project(
    db: AsyncSession,
    name: str,
) -> MediaProject:
    """Create a test project."""
    project = MediaProject(name=name)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def create_test_session(
    db: AsyncSession,
    project: MediaProject,
    label: str,
    duration_seconds: float = 300.0,
) -> tuple[MediaSession, MediaAsset]:
    """Create a test session with media asset."""
    asset = MediaAsset(
        project_id=project.id,
        storage_uri=f"local://smoke/{uuid4()}.mp4",
        original_filename=f"{label.lower().replace(' ', '_')}.mp4",
        byte_size=1000000,
        sha256=hashlib.sha256(f"smoke_{label}".encode()).hexdigest(),
        duration_seconds=duration_seconds,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    session = MediaSession(
        project_id=project.id,
        media_id=asset.id,
        label=label,
        notes_filename=f"{label.lower().replace(' ', '_')}.txt",
        raw_notes=f"{label} note 1\n{label} note 2",
        status="ready",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # Add notes
    for i in range(1, 3):
        note = Note(session_id=session.id, order=i, raw=f"{label} note {i}")
        db.add(note)
    await db.commit()

    return session, asset


async def create_transcription(
    db: AsyncSession,
    session: MediaSession,
    media: MediaAsset,
    segment_count: int = 5,
) -> Transcription:
    """Create a completed transcription with segments."""
    transcription = Transcription(
        project_id=session.project_id,
        session_id=session.id,
        media_id=media.id,
        provider="fake",
        model="fake",
        status="completed",
        configuration={},
        configuration_fingerprint=hashlib.sha256(f"trans_{session.id}".encode()).hexdigest(),
        duration_seconds=media.duration_seconds,
    )
    db.add(transcription)
    await db.commit()
    await db.refresh(transcription)

    assert media.duration_seconds is not None
    segment_duration = media.duration_seconds / segment_count
    for i in range(segment_count):
        segment = TranscriptSegment(
            transcription_id=transcription.id,
            sequence=i + 1,
            start_seconds=i * segment_duration,
            end_seconds=(i + 1) * segment_duration,
            text=f"Transcript segment {i + 1} from {session.label}",
        )
        db.add(segment)
    await db.commit()

    return transcription


async def create_visual_index(
    db: AsyncSession,
    session: MediaSession,
    media: MediaAsset,
    scene_count: int = 3,
) -> VisualIndex:
    """Create a completed visual index with scenes."""
    visual_index = VisualIndex(
        project_id=session.project_id,
        session_id=session.id,
        media_id=media.id,
        status="completed",
        detector="ffmpeg_scdet",
        configuration={},
        configuration_fingerprint=hashlib.sha256(f"vis_{session.id}".encode()).hexdigest(),
    )
    db.add(visual_index)
    await db.commit()
    await db.refresh(visual_index)

    assert media.duration_seconds is not None
    scene_duration = media.duration_seconds / scene_count
    for i in range(scene_count):
        scene = SceneSegment(
            visual_index_id=visual_index.id,
            sequence=i + 1,
            start_seconds=i * scene_duration,
            end_seconds=(i + 1) * scene_duration,
            duration_seconds=scene_duration,
        )
        db.add(scene)
    await db.commit()

    return visual_index


async def create_temporal_index(
    db: AsyncSession,
    session: MediaSession,
    media: MediaAsset,
    transcription: Transcription,
    visual_index: VisualIndex,
    embedding_provider: FakeEmbeddingProvider,
    segment_count: int = 5,
    content_prefix: str = "",
) -> TemporalIndex:
    """Create a completed temporal index with embedded segments."""
    temporal_index = TemporalIndex(
        project_id=session.project_id,
        session_id=session.id,
        media_id=media.id,
        transcription_id=transcription.id,
        visual_index_id=visual_index.id,
        status="completed",
        configuration={},
        configuration_fingerprint=hashlib.sha256(f"temp_{session.id}".encode()).hexdigest(),
        embedding_provider=embedding_provider.name,
        embedding_model="fake-model",
        embedding_dimensions=EMBEDDING_VECTOR_DIMENSIONS,
    )
    db.add(temporal_index)
    await db.commit()
    await db.refresh(temporal_index)

    assert media.duration_seconds is not None
    segment_duration = media.duration_seconds / segment_count
    for i in range(segment_count):
        text = f"{content_prefix}Temporal segment {i + 1} with Geralt learning Aard combat gameplay"
        segment = TemporalSegment(
            temporal_index_id=temporal_index.id,
            sequence=i + 1,
            start_seconds=i * segment_duration,
            end_seconds=(i + 1) * segment_duration,
            duration_seconds=segment_duration,
            text=text,
            search_text=text,
            embedding=embedding_provider.fake_embedding(i + 1),
            embedding_status="completed",
        )
        db.add(segment)
    await db.commit()

    return temporal_index


async def run_session_search_smoke(db: AsyncSession) -> bool:
    """Run session search smoke test."""
    log("=" * 60)
    log("SESSION SEARCH SMOKE TEST")
    log("=" * 60)

    provider = FakeEmbeddingProvider(name="fake", dimensions=EMBEDDING_VECTOR_DIMENSIONS)

    # Create test data
    log("Creating test project and session...")
    project = await create_test_project(db, "Session Search Test")
    session, media = await create_test_session(db, project, "Test Session")

    transcription = await create_transcription(db, session, media)
    visual_index = await create_visual_index(db, session, media)
    temporal_index = await create_temporal_index(
        db, session, media, transcription, visual_index, provider
    )

    log(f"Created session {session.id}")
    log(f"  Temporal index: {temporal_index.id}")

    # Run session search
    log("\nRunning session search...")
    service = MediaSearchService(db, provider)
    request = MediaSearchRequest(query="Geralt learning Aard", max_results=5)

    response = await service.search_session(session.id, request)

    log(f"  Query: '{request.query}'")
    log(f"  Scope: {response.scope}")
    log(f"  Results: {len(response.results)}")
    log(f"  Searched sessions: {response.searched_sessions}")

    # Validate results
    success = True

    if response.scope != "session":
        log(f"  ERROR: Expected scope='session', got '{response.scope}'")
        success = False

    if response.session_id != session.id:
        log("  ERROR: Session ID mismatch")
        success = False

    if response.searched_sessions != 1:
        log("  ERROR: Expected searched_sessions=1")
        success = False

    if len(response.results) == 0:
        log("  ERROR: Expected at least 1 result")
        success = False

    # Check ranking
    if len(response.results) > 1:
        scores = [r.score for r in response.results]
        if scores != sorted(scores, reverse=True):
            log("  ERROR: Results not ranked by score")
            success = False

    # Check result structure
    if response.results:
        result = response.results[0]
        log("\n  Top result:")
        log(f"    Session: {result.session_label}")
        log(f"    Range: {result.start_seconds:.1f}s - {result.end_seconds:.1f}s")
        log(f"    Score: {result.score:.3f}")
        log(f"    Rank: {result.rank}")

        if result.session_id != session.id:
            log("    ERROR: Result session_id mismatch")
            success = False

    # Verify embedding was called only once
    if provider.embed_call_count != 1:
        log(f"  ERROR: Expected 1 embedding call, got {provider.embed_call_count}")
        success = False
    else:
        log("\n  ✓ Query embedded exactly once")

    # Test zero results
    log("\nTesting zero results case...")
    zero_request = MediaSearchRequest(query="nonexistent content xyz", min_score=1.0)
    zero_response = await service.search_session(session.id, zero_request)

    if len(zero_response.results) != 0:
        log(f"  ERROR: Expected 0 results, got {len(zero_response.results)}")
        success = False
    else:
        log("  ✓ Zero results handled correctly")

    # Cleanup
    log("\nCleaning up test data...")
    await db.delete(project)
    await db.commit()

    if success:
        log("✓ SESSION SEARCH SMOKE PASSED")
    else:
        log("✗ SESSION SEARCH SMOKE FAILED")

    return success


async def run_project_search_smoke(db: AsyncSession) -> bool:
    """Run project search smoke test."""
    log("\n" + "=" * 60)
    log("PROJECT SEARCH SMOKE TEST")
    log("=" * 60)

    provider = FakeEmbeddingProvider(name="fake", dimensions=EMBEDDING_VECTOR_DIMENSIONS)

    # Create test data with multiple sessions
    log("Creating test project with multiple sessions...")
    project = await create_test_project(db, "Project Search Test")

    sessions = []
    temporal_indexes = []

    for i in range(1, 3):
        session, media = await create_test_session(
            db, project, f"Session {i:03d}", duration_seconds=300.0 + i * 50
        )
        transcription = await create_transcription(db, session, media)
        visual_index = await create_visual_index(db, session, media)
        temporal_index = await create_temporal_index(
            db, session, media, transcription, visual_index, provider,
            content_prefix=f"Session{i}: "
        )

        sessions.append(session)
        temporal_indexes.append(temporal_index)

        log(f"  Created session {i}: {session.id}")

    # Run project search
    log("\nRunning project search...")
    service = MediaSearchService(db, provider)
    request = MediaSearchRequest(query="Geralt combat", max_results=10)

    response = await service.search_project(project.id, request)

    log(f"  Query: '{request.query}'")
    log(f"  Scope: {response.scope}")
    log(f"  Results: {len(response.results)}")
    log(f"  Searched sessions: {response.searched_sessions}")
    log(f"  Skipped sessions: {response.skipped_sessions}")

    # Validate results
    success = True

    if response.scope != "project":
        log(f"  ERROR: Expected scope='project', got '{response.scope}'")
        success = False

    if response.session_id is not None:
        log("  ERROR: session_id should be None for project search")
        success = False

    if response.searched_sessions < 1:
        log("  ERROR: Expected at least 1 searched session")
        success = False

    # Check global ranking
    if len(response.results) > 1:
        scores = [r.score for r in response.results]
        if scores != sorted(scores, reverse=True):
            log("  ERROR: Results not globally ranked by score")
            success = False

        ranks = [r.rank for r in response.results]
        expected_ranks = list(range(1, len(response.results) + 1))
        if ranks != expected_ranks:
            log(f"  ERROR: Ranks not sequential: {ranks}")
            success = False

    # Check session summaries
    if not response.session_summaries:
        log("  ERROR: Expected session summaries")
        success = False
    else:
        log("\n  Session summaries:")
        for summary in response.session_summaries:
            status = "searched" if summary.searched else f"skipped: {summary.skip_reason}"
            log(f"    {summary.label}: {status} ({summary.result_count} results)")

    # Check results
    if response.results:
        log("\n  Top results:")
        for i, result in enumerate(response.results[:5]):
            log(f"    {i+1}. {result.session_label}: {result.start_seconds:.1f}s - {result.end_seconds:.1f}s (score={result.score:.3f})")

    # Verify query was embedded only once (same compatibility group)
    expected_embed_calls = 1  # All sessions use same embedding config
    if provider.embed_call_count != expected_embed_calls:
        log(f"\n  ERROR: Expected {expected_embed_calls} embedding call(s), got {provider.embed_call_count}")
        success = False
    else:
        log("\n  ✓ Query embedded exactly once for compatibility group")

    # Test session filter
    log("\nTesting session filter...")
    filter_request = MediaSearchRequest(
        query="Geralt",
        max_results=5,
        session_ids=[sessions[0].id],
    )
    filter_response = await service.search_project(project.id, filter_request)

    for result in filter_response.results:
        if result.session_id != sessions[0].id:
            log("  ERROR: Filtered result from wrong session")
            success = False
            break
    else:
        log("  ✓ Session filter works correctly")

    # Cleanup
    log("\nCleaning up test data...")
    await db.delete(project)
    await db.commit()

    if success:
        log("✓ PROJECT SEARCH SMOKE PASSED")
    else:
        log("✗ PROJECT SEARCH SMOKE FAILED")

    return success


async def main() -> int:
    """Run all smoke tests."""
    log("Media Search Smoke Test")
    log("=" * 60)

    # Create async engine
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as db:
        try:
            session_success = await run_session_search_smoke(db)
            project_success = await run_project_search_smoke(db)

            log("\n" + "=" * 60)
            log("SUMMARY")
            log("=" * 60)

            if session_success and project_success:
                log("✓ ALL SMOKE TESTS PASSED")
                return 0
            else:
                log("✗ SOME SMOKE TESTS FAILED")
                return 1

        except Exception as e:
            log(f"Smoke test error: {e}")
            import traceback
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
