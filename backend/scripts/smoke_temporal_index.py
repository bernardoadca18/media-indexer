"""End-to-end temporal index smoke using PostgreSQL and a fake embedding provider.

Run from backend after migrations:
    python scripts/smoke_temporal_index.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from uuid import uuid4

from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    DerivedAsset,
    MediaAsset,
    MediaProject,
    MediaSession,
    Note,
    SceneSegment,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.services.candidate_retrieval import (
    CandidateRetrievalConfig,
    CandidateRetrievalService,
)
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.temporal_index import (
    TemporalIndexProcessor,
    TemporalIndexRequest,
    TemporalIndexService,
    TemporalSearchService,
)


class SmokeEmbeddingProvider:
    name = "smoke"

    async def embed(
        self,
        texts,
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        vectors = []
        for text_value in texts:
            head = [1.0, 0.0] if "laboratory" in text_value.lower() else [0.0, 1.0]
            vectors.append(head + [0.0] * (expected_dimensions - len(head)))
        return vectors


async def run() -> None:
    project_id = None
    provider = SmokeEmbeddingProvider()
    async with AsyncSessionLocal() as db:
        try:
            project = MediaProject(name=f"temporal-smoke-{uuid4()}")
            db.add(project)
            await db.flush()
            project_id = project.id
            session_id, media_id = uuid4(), uuid4()
            media = MediaAsset(
                id=media_id,
                project_id=project.id,
                storage_uri=f"local://smoke/{media_id}.mp4",
                original_filename="smoke.mp4",
                content_type="video/mp4",
                byte_size=1,
                sha256="d" * 64,
                duration_seconds=90.0,
            )
            session = MediaSession(
                id=session_id,
                project_id=project.id,
                media_id=media.id,
                label="temporal smoke",
                notes_filename="notes.txt",
                raw_notes="raw",
                status="ready",
            )
            note = Note(
                session_id=session.id,
                order=1,
                raw="Geralt protects the laboratory",
            )
            transcription = Transcription(
                project_id=project.id,
                session_id=session.id,
                media_id=media.id,
                provider="smoke",
                model="smoke",
                status="completed",
                configuration={},
                configuration_fingerprint=uuid4().hex,
                duration_seconds=90.0,
            )
            visual_index = VisualIndex(
                project_id=project.id,
                session_id=session.id,
                media_id=media.id,
                status="completed",
                detector="smoke",
                configuration={},
                configuration_fingerprint=uuid4().hex,
            )
            db.add(media)
            await db.flush()
            db.add(session)
            await db.flush()
            db.add(note)
            await db.flush()
            db.add_all([transcription, visual_index])
            await db.flush()
            db.add_all(
                [
                    TranscriptSegment(
                        transcription_id=transcription.id,
                        sequence=1,
                        start_seconds=5.0,
                        end_seconds=20.0,
                        text="protect the laboratory",
                    ),
                    TranscriptSegment(
                        transcription_id=transcription.id,
                        sequence=2,
                        start_seconds=50.0,
                        end_seconds=60.0,
                        text="walk through the forest",
                    ),
                    SceneSegment(
                        visual_index_id=visual_index.id,
                        sequence=1,
                        start_seconds=0.0,
                        end_seconds=45.0,
                        duration_seconds=45.0,
                    ),
                    SceneSegment(
                        visual_index_id=visual_index.id,
                        sequence=2,
                        start_seconds=45.0,
                        end_seconds=90.0,
                        duration_seconds=45.0,
                    ),
                    DerivedAsset(
                        project_id=project.id,
                        session_id=session.id,
                        media_id=media.id,
                        visual_index_id=visual_index.id,
                        kind="representative_frame",
                        storage_uri=f"local://smoke/frame-{visual_index.id}.jpg",
                        mime_type="image/jpeg",
                        byte_size=1,
                        sha256="e" * 64,
                        width=640,
                        height=360,
                        timestamp_seconds=70.0,
                    ),
                ]
            )
            await db.commit()

            ensured = await TemporalIndexService(db, max_attempts=3).ensure(
                session.id,
                TemporalIndexRequest(
                    transcription_id=transcription.id,
                    visual_index_id=visual_index.id,
                    target_duration_seconds=30.0,
                    min_duration_seconds=15.0,
                    max_duration_seconds=60.0,
                    embedding_provider=provider.name,
                    embedding_model="smoke",
                    embedding_dimensions=EMBEDDING_VECTOR_DIMENSIONS,
                ),
            )
            registry = ProcessorRegistry(
                {
                    "temporal_index": lambda inner_db: TemporalIndexProcessor(
                        inner_db,
                        provider,
                        batch_size=2,
                    )
                }
            )
            if not await JobExecutor(db, registry).execute(ensured.job.id):
                raise RuntimeError("temporal index job failed")
            hits = await TemporalSearchService(db, provider).search(
                ensured.temporal_index.id,
                "defend the laboratory",
                2,
            )
            if not hits or "laboratory" not in hits[0].segment.search_text:
                raise RuntimeError("semantic smoke returned an unexpected first segment")
            retrieval_service = CandidateRetrievalService(db, provider)
            retrieval_config = CandidateRetrievalConfig(
                context_before_seconds=5.0,
                context_after_seconds=10.0,
            )
            query_result = await retrieval_service.retrieve_query(
                ensured.temporal_index.id,
                "defend the laboratory",
                retrieval_config,
            )
            repeated = await retrieval_service.retrieve_query(
                ensured.temporal_index.id,
                "defend the laboratory",
                retrieval_config,
            )
            note_result = await retrieval_service.retrieve_notes(
                ensured.temporal_index.id,
                [note.id],
                retrieval_config,
            )
            if not query_result.candidates or not note_result.candidates:
                raise RuntimeError("candidate retrieval smoke returned no candidates")
            if repeated.created or repeated.run.id != query_result.run.id:
                raise RuntimeError("candidate retrieval smoke was not idempotent")
            first_candidate = query_result.candidates[0]
            if first_candidate.start_seconds > 5.0 or first_candidate.end_seconds < 20.0:
                raise RuntimeError("candidate does not cover the expected evidence")
            print(
                {
                    "temporal_index_id": str(ensured.temporal_index.id),
                    "retrieval_run_id": str(query_result.run.id),
                    "first_candidate": [
                        first_candidate.start_seconds,
                        first_candidate.end_seconds,
                    ],
                    "first_score": first_candidate.score,
                    "note_candidate_count": len(note_result.candidates),
                    "idempotent": True,
                }
            )
        finally:
            await db.rollback()
            if project_id is not None:
                await db.execute(delete(MediaProject).where(MediaProject.id == project_id))
                await db.commit()


if __name__ == "__main__":
    asyncio.run(run())
