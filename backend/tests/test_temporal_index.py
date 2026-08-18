from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    DerivedAsset,
    MediaAsset,
    MediaProject,
    MediaSession,
    SceneSegment,
    TemporalSegmentFrameLink,
    TemporalSegmentSceneLink,
    TemporalSegmentTranscriptLink,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.repositories.temporal_index import TemporalIndexRepository
from app.services.embedding_provider import EmbeddingDimensionError
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.temporal_index import (
    TemporalIndexDimensionError,
    TemporalIndexProcessor,
    TemporalIndexRequest,
    TemporalIndexService,
    TemporalPrerequisiteError,
    TemporalSearchService,
    build_temporal_segments,
)


class FakeEmbeddingProvider:
    name = "fake"

    def __init__(self, *, fail: bool = False, dimensions: int = EMBEDDING_VECTOR_DIMENSIONS):
        self.fail = fail
        self.dimensions = dimensions
        self.batches: list[list[str]] = []

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        assert model == "fake-model"
        assert expected_dimensions == EMBEDDING_VECTOR_DIMENSIONS
        self.batches.append(list(texts))
        if self.fail:
            raise RuntimeError("embedding provider interrupted")
        return [
            [float(index + 1)] + [0.0] * (self.dimensions - 1)
            for index, _ in enumerate(texts)
        ]


async def seed_ready_prerequisites(db):
    project = MediaProject(name=str(uuid4()))
    db.add(project)
    await db.flush()
    session_id, media_id = uuid4(), uuid4()
    media = MediaAsset(
        id=media_id,
        project_id=project.id,
        storage_uri=f"local://projects/{project.id}/sessions/{session_id}/source/gameplay.mp4",
        original_filename="gameplay.mp4",
        content_type="video/mp4",
        byte_size=5,
        sha256="a" * 64,
        duration_seconds=120.0,
    )
    session = MediaSession(
        id=session_id,
        project_id=project.id,
        media_id=media_id,
        label="session",
        notes_filename="notes.txt",
        raw_notes="raw",
        status="ready",
    )
    transcription = Transcription(
        project_id=project.id,
        session_id=session_id,
        media_id=media_id,
        provider="fake-asr",
        model="small",
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        duration_seconds=120.0,
    )
    visual_index = VisualIndex(
        project_id=project.id,
        session_id=session_id,
        media_id=media_id,
        status="completed",
        detector="fake",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add_all([media, session, transcription, visual_index])
    await db.flush()

    transcripts = [
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
            start_seconds=28.0,
            end_seconds=35.0,
            text="Geralt uses Aard",
        ),
        TranscriptSegment(
            transcription_id=transcription.id,
            sequence=3,
            start_seconds=65.0,
            end_seconds=72.0,
            text="escape through the gate",
        ),
    ]
    scenes = [
        SceneSegment(
            visual_index_id=visual_index.id,
            sequence=sequence,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
        )
        for sequence, (start, end) in enumerate(
            [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0), (90.0, 120.0)],
            start=1,
        )
    ]
    frames = [
        DerivedAsset(
            project_id=project.id,
            session_id=session.id,
            media_id=media.id,
            visual_index_id=visual_index.id,
            kind="representative_frame",
            storage_uri=f"local://frames/{visual_index.id}/{timestamp}.jpg",
            mime_type="image/jpeg",
            byte_size=4,
            sha256=f"{index:x}" * 64,
            width=640,
            height=360,
            timestamp_seconds=timestamp,
            asset_metadata={"origins": ["periodic"]},
        )
        for index, timestamp in enumerate([10.0, 35.0, 72.0, 119.0, 120.0], start=1)
    ]
    db.add_all([*transcripts, *scenes, *frames])
    await db.commit()
    return session, transcription, visual_index, transcripts, scenes, frames


def request(
    transcription,
    visual_index,
    *,
    target: float = 30.0,
    dimensions: int = EMBEDDING_VECTOR_DIMENSIONS,
) -> TemporalIndexRequest:
    return TemporalIndexRequest(
        transcription_id=transcription.id,
        visual_index_id=visual_index.id,
        target_duration_seconds=target,
        min_duration_seconds=15.0,
        max_duration_seconds=60.0,
        embedding_provider="fake",
        embedding_model="fake-model",
        embedding_dimensions=dimensions,
    )


def registry(provider: FakeEmbeddingProvider, batch_size: int = 2) -> ProcessorRegistry:
    return ProcessorRegistry(
        {
            "temporal_index": lambda db: TemporalIndexProcessor(
                db,
                provider,
                batch_size=batch_size,
            )
        }
    )


async def test_segmentation_covers_timeline_and_associates_normalized_evidence(
    session_factory,
) -> None:
    async with session_factory() as db:
        _, _, _, transcripts, scenes, frames = await seed_ready_prerequisites(db)
        definitions = build_temporal_segments(
            120.0,
            transcripts,
            scenes,
            frames,
            target_duration_seconds=30.0,
            min_duration_seconds=15.0,
            max_duration_seconds=60.0,
        )

        assert [(item.start_seconds, item.end_seconds) for item in definitions] == [
            (0.0, 35.0),
            (35.0, 72.0),
            (72.0, 90.0),
            (90.0, 120.0),
        ]
        assert definitions[0].text == "protect the laboratory Geralt uses Aard"
        assert definitions[1].text == "escape through the gate"
        assert definitions[2].text == ""
        assert definitions[0].transcript_segment_ids == (transcripts[0].id, transcripts[1].id)
        assert definitions[1].transcript_segment_ids == (transcripts[2].id,)
        assert definitions[1].frame_asset_ids == (frames[1].id,)
        assert definitions[2].frame_asset_ids == (frames[2].id,)
        assert definitions[3].frame_asset_ids == (frames[3].id, frames[4].id)
        assert definitions[0].scene_segment_ids == (scenes[0].id, scenes[1].id)
        assert definitions[-1].end_seconds == 120.0


async def test_temporal_job_persists_segments_embeddings_and_silence(session_factory) -> None:
    provider = FakeEmbeddingProvider()
    async with session_factory() as db:
        session, transcription, visual_index, _, _, _ = await seed_ready_prerequisites(db)
        created = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            request(transcription, visual_index),
        )

        assert created.created and created.enqueue
        assert created.job.temporal_index_id == created.temporal_index.id
        assert await JobExecutor(db, registry(provider, batch_size=1)).execute(created.job.id)

        await db.refresh(created.temporal_index)
        await db.refresh(created.job)
        await db.refresh(session)
        records, total = await TemporalIndexRepository(db).segments(
            created.temporal_index.id, 0, 100
        )

        assert created.temporal_index.status == "completed"
        assert created.job.status == "completed"
        assert created.job.attempt_count == 1
        assert session.status == "ready"
        assert total == 4
        assert [record.segment.sequence for record in records] == [1, 2, 3, 4]
        assert [record.segment.embedding_status for record in records] == [
            "completed",
            "completed",
            "not_applicable",
            "not_applicable",
        ]
        assert records[2].segment.embedding is None
        assert provider.batches == [
            ["protect the laboratory Geralt uses Aard"],
            ["escape through the gate"],
        ]
        assert records[0].transcript_segment_ids
        assert records[0].scene_segment_ids
        assert records[0].frame_asset_ids


async def test_temporal_index_idempotency_config_and_prerequisites(session_factory) -> None:
    async with session_factory() as db:
        session, transcription, visual_index, _, _, _ = await seed_ready_prerequisites(db)
        service = TemporalIndexService(db, max_attempts=3)
        first = await service.ensure(session.id, request(transcription, visual_index))
        same = await service.ensure(session.id, request(transcription, visual_index))
        different = await service.ensure(
            session.id,
            request(transcription, visual_index, target=25.0),
        )

        assert same.temporal_index.id == first.temporal_index.id
        assert same.job.id == first.job.id
        assert not same.created and not same.enqueue
        assert different.temporal_index.id != first.temporal_index.id

        visual_index.status = "failed"
        await db.commit()
        with pytest.raises(TemporalPrerequisiteError, match="visual index must be completed"):
            await service.ensure(session.id, request(transcription, visual_index))

        visual_index.status = "completed"
        await db.commit()
        with pytest.raises(TemporalIndexDimensionError, match="database vector dimension"):
            await service.ensure(
                session.id,
                request(
                    transcription,
                    visual_index,
                    dimensions=EMBEDDING_VECTOR_DIMENSIONS + 1,
                ),
            )


async def test_failure_cleanup_retry_and_dimension_validation(session_factory) -> None:
    async with session_factory() as db:
        session, transcription, visual_index, _, _, _ = await seed_ready_prerequisites(db)
        base_request = request(transcription, visual_index)
        alternate_request = request(transcription, visual_index, target=25.0)
        result = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            base_request,
        )
        assert not await JobExecutor(
            db,
            registry(FakeEmbeddingProvider(fail=True)),
        ).execute(result.job.id)

        await db.refresh(result.temporal_index)
        await db.refresh(result.job)
        await db.refresh(session)
        _, total = await TemporalIndexRepository(db).segments(result.temporal_index.id, 0, 100)
        assert result.temporal_index.status == "failed"
        assert result.job.status == "failed"
        assert session.status == "ready"
        assert total == 0
        for link_model in (
            TemporalSegmentTranscriptLink,
            TemporalSegmentSceneLink,
            TemporalSegmentFrameLink,
        ):
            assert await db.scalar(select(func.count()).select_from(link_model)) == 0

        retry = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            base_request,
        )
        assert retry.enqueue and not retry.created
        assert await JobExecutor(db, registry(FakeEmbeddingProvider())).execute(retry.job.id)
        _, total = await TemporalIndexRepository(db).segments(retry.temporal_index.id, 0, 100)
        assert retry.job.attempt_count == 2
        assert total == 4

        other = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            alternate_request,
        )
        assert not await JobExecutor(
            db,
            registry(FakeEmbeddingProvider(dimensions=7)),
        ).execute(other.job.id)
        await db.refresh(other.job)
        assert "expected 1024" in (other.job.error or "")


async def test_stale_temporal_job_recovery_preserves_ready_session(session_factory) -> None:
    async with session_factory() as db:
        session, transcription, visual_index, _, _, _ = await seed_ready_prerequisites(db)
        result = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            request(transcription, visual_index),
        )
        result.job.status = "processing"
        result.job.attempt_count = 1
        result.job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        result.temporal_index.status = "processing"
        await db.commit()

        recovered = await JobExecutor(db, ProcessorRegistry()).recover(
            datetime.now(UTC) - timedelta(minutes=5)
        )
        await db.refresh(result.job)
        await db.refresh(result.temporal_index)
        await db.refresh(session)
        assert recovered == [result.job.id]
        assert result.job.status == "pending"
        assert result.job.stage == "recovered"
        assert result.temporal_index.status == "pending"
        assert session.status == "ready"

async def test_query_dimension_mismatch_fails_before_similarity(session_factory) -> None:
    async with session_factory() as db:
        session, transcription, visual_index, _, _, _ = await seed_ready_prerequisites(db)
        result = await TemporalIndexService(db, max_attempts=3).ensure(
            session.id,
            request(transcription, visual_index),
        )
        assert await JobExecutor(
            db,
            registry(FakeEmbeddingProvider()),
        ).execute(result.job.id)

        with pytest.raises(EmbeddingDimensionError, match="7 dimensions; expected 1024"):
            await TemporalSearchService(
                db,
                FakeEmbeddingProvider(dimensions=7),
            ).search(result.temporal_index.id, "protect the laboratory", 5)

