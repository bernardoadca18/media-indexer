from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from app.models import MediaAsset, MediaProject, MediaSession
from app.repositories.transcription import TranscriptionRepository
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.storage import LocalStorage
from app.services.transcription import (
    TranscriptionProcessor,
    TranscriptionRequest,
    TranscriptionService,
)
from app.services.transcription_provider import (
    SegmentResult,
    TranscriptionConfig,
    TranscriptionStream,
    WordResult,
)


class FakeProvider:
    name = "fake"

    def __init__(self, *, fail_after_first: bool = False):
        self.fail_after_first = fail_after_first
        self.calls = 0

    async def transcribe(self, source: Path, config: TranscriptionConfig) -> TranscriptionStream:
        assert source.read_bytes() == b"video"
        assert config.model == "small"
        self.calls += 1

        async def segments() -> AsyncIterator[SegmentResult]:
            yield SegmentResult(
                start=1.25,
                end=2.5,
                text=" Geralt",
                avg_logprob=-0.2,
                no_speech_prob=0.01,
                compression_ratio=1.1,
                words=[WordResult(" Geralt", 1.25, 1.8, 0.96)],
            )
            if self.fail_after_first:
                raise RuntimeError("provider interrupted")
            yield SegmentResult(start=3.0, end=4.75, text=" of Rivia")

        return TranscriptionStream(
            language="en",
            duration_seconds=10.0,
            metadata={"language_probability": 0.99},
            segments=segments(),
        )


async def ready_session(db, storage: LocalStorage) -> MediaSession:
    project = MediaProject(name=str(uuid4()))
    db.add(project)
    await db.flush()
    session_id, media_id = uuid4(), uuid4()
    media = MediaAsset(
        id=media_id,
        project_id=project.id,
        storage_uri=(f"local://projects/{project.id}/sessions/{session_id}/source/gameplay.mp4"),
        original_filename="gameplay.mp4",
        content_type="video/mp4",
        byte_size=5,
        sha256="a" * 64,
        duration_seconds=10.0,
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
    source = storage.resolve(media.storage_uri)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    db.add_all([media, session])
    await db.commit()
    return session


def request() -> TranscriptionRequest:
    return TranscriptionRequest(
        model="small",
        language=None,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
        compute_type="auto",
    )


def registry(storage: LocalStorage, provider: FakeProvider) -> ProcessorRegistry:
    return ProcessorRegistry(
        {"transcription": lambda db: TranscriptionProcessor(db, storage, provider, batch_size=1)}
    )


async def test_transcription_persists_order_timestamps_words_and_language(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    provider = FakeProvider()
    async with session_factory() as db:
        session = await ready_session(db, storage)
        created = await TranscriptionService(db, max_attempts=3).ensure(session.id, request())
        assert created.created
        assert created.enqueue
        assert created.job.job_type == "transcription"

        assert await JobExecutor(db, registry(storage, provider)).execute(created.job.id)
        await db.refresh(created.transcription)
        await db.refresh(created.job)
        items, total = await TranscriptionRepository(db).segments(created.transcription.id, 0, 100)

        assert provider.calls == 1
        assert created.transcription.status == "completed"
        assert created.transcription.language_detected == "en"
        assert created.transcription.duration_seconds == 10.0
        assert created.transcription.provider_metadata == {"language_probability": 0.99}
        assert created.job.status == "completed"
        assert created.job.attempt_count == 1
        assert total == 2
        assert [item.sequence for item in items] == [1, 2]
        assert [(item.start_seconds, item.end_seconds) for item in items] == [
            (1.25, 2.5),
            (3.0, 4.75),
        ]
        assert items[0].word_timestamps == [
            {
                "word": " Geralt",
                "start": 1.25,
                "end": 1.8,
                "probability": 0.96,
            }
        ]


async def test_equivalent_request_is_idempotent_but_other_model_is_distinct(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        service = TranscriptionService(db, max_attempts=3)
        first = await service.ensure(session.id, request())
        same = await service.ensure(session.id, request())
        different = await service.ensure(
            session.id,
            TranscriptionRequest(
                model="large-v3",
                language=None,
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                compute_type="auto",
            ),
        )

        assert same.transcription.id == first.transcription.id
        assert same.job.id == first.job.id
        assert not same.created
        assert different.transcription.id != first.transcription.id
        assert different.transcription.configuration_fingerprint != (
            first.transcription.configuration_fingerprint
        )


async def test_failure_keeps_session_ready_and_retry_replaces_partial_segments(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        result = await TranscriptionService(db, max_attempts=3).ensure(session.id, request())
        failing = FakeProvider(fail_after_first=True)
        assert not await JobExecutor(db, registry(storage, failing)).execute(result.job.id)
        await db.refresh(result.transcription)
        await db.refresh(result.job)
        await db.refresh(session)
        _, partial_total = await TranscriptionRepository(db).segments(
            result.transcription.id, 0, 100
        )

        assert result.transcription.status == "failed"
        assert result.job.status == "failed"
        assert "provider interrupted" in (result.job.error or "")
        assert session.status == "ready"
        assert partial_total == 1

        retry = await TranscriptionService(db, max_attempts=3).ensure(session.id, request())
        assert retry.transcription.id == result.transcription.id
        assert retry.job.id == result.job.id
        assert retry.enqueue
        assert await JobExecutor(db, registry(storage, FakeProvider())).execute(retry.job.id)

        await db.refresh(retry.transcription)
        await db.refresh(retry.job)
        items, total = await TranscriptionRepository(db).segments(retry.transcription.id, 0, 100)
        assert retry.transcription.status == "completed"
        assert retry.job.attempt_count == 2
        assert total == 2
        assert [item.sequence for item in items] == [1, 2]


async def test_transcription_records_are_queryable_without_segments(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        result = await TranscriptionService(db, max_attempts=3).ensure(session.id, request())
        listed = await TranscriptionRepository(db).list_for_session(session.id)
        loaded = await TranscriptionRepository(db).get(result.transcription.id)
        assert [item.id for item in listed] == [result.transcription.id]
        assert loaded is not None
        assert loaded.status == "pending"
