from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AlignmentSubject,
    CandidateAlignment,
    DerivedAsset,
    MediaAsset,
    MediaProject,
    MediaSession,
    Note,
    RetrievalCandidate,
    RetrievalRun,
    TemporalIndex,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.repositories.alignment import AlignmentRepository
from app.services.alignment import (
    AlignmentConfig,
    AlignmentProcessor,
    AlignmentRequest,
    AlignmentService,
    refinement_window,
    sample_frame_timestamps,
)
from app.services.alignment_provider import (
    AlignmentAnchor,
    AlignmentProviderError,
    AlignmentProviderRequest,
    AlignmentProviderResponse,
    AlignmentProviderResult,
    InvalidAlignmentOutputError,
    validate_result_bounds,
)
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.storage import LocalStorage
from app.services.visual_tools import ExtractedFrame


class FakeFrameExtractor:
    name = "fake"

    def __init__(self):
        self.timestamps: list[float] = []

    async def extract(
        self,
        source: Path,
        timestamp_seconds: float,
        width: int,
        image_format: str,
        jpeg_quality: int,
    ) -> ExtractedFrame:
        self.timestamps.append(timestamp_seconds)
        assert width > 0
        assert image_format == "jpeg"
        assert 1 <= jpeg_quality <= 100
        return ExtractedFrame(
            content=f"jpeg:{timestamp_seconds:.3f}".encode(),
            mime_type="image/jpeg",
            width=width,
            height=360,
        )


class FakeAlignmentProvider:
    name = "fake"

    def __init__(
        self,
        results: list[AlignmentProviderResult] | None = None,
        *,
        error: Exception | None = None,
    ):
        self.results = list(results or [])
        self.error = error
        self.requests: list[AlignmentProviderRequest] = []

    async def align(
        self,
        request: AlignmentProviderRequest,
        *,
        model: str,
    ) -> AlignmentProviderResponse:
        assert model
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if not self.results:
            raise AssertionError("fake provider has no scripted result")
        return AlignmentProviderResponse(
            result=self.results.pop(0),
            provider_metadata={"request_id": f"fake-{len(self.requests)}"},
            usage_metadata={"total_tokens": 10},
            latency_seconds=0.01,
        )


def matched(start: float, end: float, confidence: float) -> AlignmentProviderResult:
    return AlignmentProviderResult(
        matched=True,
        start_seconds=start,
        end_seconds=end,
        confidence=confidence,
        anchors=[
            AlignmentAnchor(
                timestamp_seconds=(start + end) / 2,
                description="visible event",
            )
        ],
        reasoning_summary="The evidence supports this interval.",
    )


def unmatched(confidence: float = 0.8) -> AlignmentProviderResult:
    return AlignmentProviderResult(
        matched=False,
        start_seconds=None,
        end_seconds=None,
        confidence=confidence,
        anchors=[],
        reasoning_summary="The event is not visible in this window.",
    )


async def seed_alignment_domain(
    db,
    *,
    candidate_count: int = 2,
    completed: bool = True,
    query: bool = False,
):
    project = MediaProject(name=f"alignment-{uuid4()}")
    db.add(project)
    await db.flush()
    media = MediaAsset(
        project_id=project.id,
        storage_uri=f"local://sources/{uuid4()}.mp4",
        original_filename="gameplay.mp4",
        content_type="video/mp4",
        byte_size=1,
        sha256="a" * 64,
        duration_seconds=180.0,
    )
    db.add(media)
    await db.flush()
    session = MediaSession(
        project_id=project.id,
        media_id=media.id,
        label="gameplay",
        notes_filename="notes.txt",
        raw_notes="  Aprendo o Aard, primeiro sinal...  ",
        status="ready",
    )
    db.add(session)
    await db.flush()
    note = Note(
        session_id=session.id,
        order=1,
        raw="  Aprendo o Aard, primeiro sinal...  ",
    )
    transcription = Transcription(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        provider="fake",
        model="small",
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        duration_seconds=180.0,
    )
    visual = VisualIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        status="completed",
        detector="fake",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add_all([note, transcription, visual])
    await db.flush()
    temporal = TemporalIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        transcription_id=transcription.id,
        visual_index_id=visual.id,
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        embedding_provider="fake",
        embedding_model="fake",
        embedding_dimensions=1024,
    )
    db.add(temporal)
    await db.flush()
    db.add_all(
        [
            TranscriptSegment(
                transcription_id=transcription.id,
                sequence=1,
                start_seconds=32.0,
                end_seconds=52.0,
                text="Geralt aprende o sinal Aard",
            ),
            TranscriptSegment(
                transcription_id=transcription.id,
                sequence=2,
                start_seconds=92.0,
                end_seconds=112.0,
                text="combate distante",
            ),
        ]
    )
    subject_key = "query" if query else f"note:{note.id}"
    subject = (
        {"subject_key": subject_key, "query": "Geralt learning Aard"}
        if query
        else {"subject_key": subject_key, "note_id": str(note.id)}
    )
    retrieval = RetrievalRun(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        temporal_index_id=temporal.id,
        kind="query" if query else "notes",
        strategy="semantic_temporal_v1",
        status="completed" if completed else "processing",
        query_text="Geralt learning Aard" if query else None,
        configuration={"subjects": [subject]},
        configuration_fingerprint=uuid4().hex,
    )
    db.add(retrieval)
    await db.flush()
    candidates = []
    for rank, (start, end, score) in enumerate(
        [(30.0, 60.0, 0.94), (90.0, 120.0, 0.78)][:candidate_count],
        start=1,
    ):
        candidate = RetrievalCandidate(
            retrieval_run_id=retrieval.id,
            note_id=None if query else note.id,
            subject_key=subject_key,
            rank=rank,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
            score=score,
            score_components={"semantic_max": score},
        )
        db.add(candidate)
        candidates.append(candidate)
    await db.commit()
    return {
        "project": project,
        "session": session,
        "media": media,
        "note": note,
        "retrieval": retrieval,
        "candidates": candidates,
    }


def test_sampling_bounds_and_refinement_are_deterministic() -> None:
    plan = sample_frame_timestamps(30.0, 60.0, 2.0, 4)
    assert plan.timestamps == (30.0, 40.0, 50.0, 60.0)
    assert plan.resolution_seconds == 10.0
    assert sample_frame_timestamps(0.0, 5.0, 2.0, 10).timestamps == (0.0, 2.0, 4.0)
    assert refinement_window(40.0, 50.0, 30.0, 60.0, 4.0) == (36.0, 54.0)
    with pytest.raises(ValueError, match="invalid alignment frame window"):
        sample_frame_timestamps(-1.0, 5.0, 2.0, 3)


def test_provider_result_contract_rejects_invalid_shapes_and_bounds() -> None:
    request = AlignmentProviderRequest(
        candidate_id=uuid4(),
        note_id=None,
        observation="event",
        candidate_start_seconds=10.0,
        candidate_end_seconds=20.0,
        retrieval_rank=1,
        retrieval_score=0.9,
        pass_name="coarse",
        timestamp_resolution_seconds=2.0,
        transcript=(),
        transcript_truncated=False,
        frames=(),
    )
    validate_result_bounds(matched(12.0, 18.0, 0.9), request)
    with pytest.raises(InvalidAlignmentOutputError, match="outside"):
        validate_result_bounds(matched(9.0, 18.0, 0.9), request)
    with pytest.raises(ValueError, match="null timestamps"):
        AlignmentProviderResult(
            matched=False,
            start_seconds=12.0,
            end_seconds=13.0,
            confidence=0.4,
        )
    with pytest.raises(ValueError):
        AlignmentProviderResult(
            matched=True,
            start_seconds=12.0,
            end_seconds=13.0,
            confidence=1.2,
        )


async def test_alignment_job_matches_early_stops_persists_frames_and_is_idempotent(
    session_factory,
    tmp_path,
) -> None:
    storage = LocalStorage(tmp_path)
    extractor = FakeFrameExtractor()
    provider = FakeAlignmentProvider([matched(36.0, 52.0, 0.95)])
    async with session_factory() as db:
        domain = await seed_alignment_domain(db)
        request = AlignmentRequest(
            model="alignment-alias",
            candidate_ids=None,
            config=AlignmentConfig(max_frames=4),
        )
        service = AlignmentService(db, provider_name="fake", max_attempts=3)
        ensured = await service.ensure(domain["retrieval"].id, request)
        repeated = await service.ensure(domain["retrieval"].id, request)
        changed = await service.ensure(
            domain["retrieval"].id,
            AlignmentRequest("different-model", None, AlignmentConfig(max_frames=4)),
        )
        assert ensured.created and ensured.enqueue
        assert not repeated.created and repeated.alignment_run.id == ensured.alignment_run.id
        assert changed.alignment_run.id != ensured.alignment_run.id

        registry = ProcessorRegistry(
            {
                "multimodal_alignment": lambda active_db: AlignmentProcessor(
                    active_db,
                    storage,
                    extractor,
                    provider,
                )
            }
        )
        assert await JobExecutor(db, registry).execute(ensured.job.id)
        await db.refresh(ensured.alignment_run)
        await db.refresh(ensured.job)
        await db.refresh(domain["session"])
        results, total = await AlignmentRepository(db).results(ensured.alignment_run.id)
        subjects = await AlignmentRepository(db).subjects(ensured.alignment_run.id)
        assets = await AlignmentRepository(db).alignment_assets(ensured.alignment_run.id)

        assert ensured.alignment_run.status == "completed"
        assert ensured.job.status == "completed"
        assert domain["session"].status == "ready"
        assert total == 2
        assert [item.status for item in results] == ["matched", "not_evaluated"]
        assert results[0].started_at is not None and results[0].completed_at is not None
        assert results[1].started_at is None
        assert results[1].completed_at is not None
        assert subjects[0].status == "matched"
        assert subjects[0].selected_candidate_alignment_id == results[0].id
        assert provider.requests[0].observation == domain["note"].raw
        assert [item.timestamp_seconds for item in assets] == [30.0, 40.0, 50.0, 60.0]
        assert all(item.kind == "alignment_frame" for item in assets)
        assert all(storage.resolve(item.storage_uri).exists() for item in assets)
        assert ensured.alignment_run.usage_metadata["provider_calls"] == 1


async def test_unmatched_and_no_candidates_are_valid_completed_outcomes(
    session_factory,
    tmp_path,
) -> None:
    async with session_factory() as db:
        domain = await seed_alignment_domain(db)
        provider = FakeAlignmentProvider([unmatched(0.8), unmatched(0.7)])
        request = AlignmentRequest(
            "alignment-alias",
            None,
            AlignmentConfig(max_frames=1),
        )
        ensured = await AlignmentService(
            db,
            provider_name="fake",
            max_attempts=3,
        ).ensure(domain["retrieval"].id, request)
        processor = AlignmentProcessor(
            db,
            LocalStorage(tmp_path),
            FakeFrameExtractor(),
            provider,
        )
        registry = ProcessorRegistry({"multimodal_alignment": lambda _: processor})
        assert await JobExecutor(db, registry).execute(ensured.job.id)
        subjects = await AlignmentRepository(db).subjects(ensured.alignment_run.id)
        results, _ = await AlignmentRepository(db).results(ensured.alignment_run.id)
        assert subjects[0].status == "unmatched"
        assert all(item.status == "unmatched" for item in results)
        assert all(item.start_seconds is None and item.end_seconds is None for item in results)

    async with session_factory() as db:
        domain = await seed_alignment_domain(db, candidate_count=0, query=True)
        provider = FakeAlignmentProvider([])
        ensured = await AlignmentService(
            db,
            provider_name="fake",
            max_attempts=3,
        ).ensure(
            domain["retrieval"].id,
            AlignmentRequest("alignment-alias", None, AlignmentConfig(max_frames=1)),
        )
        registry = ProcessorRegistry(
            {
                "multimodal_alignment": lambda active_db: AlignmentProcessor(
                    active_db,
                    LocalStorage(tmp_path),
                    FakeFrameExtractor(),
                    provider,
                )
            }
        )
        assert await JobExecutor(db, registry).execute(ensured.job.id)
        subjects = await AlignmentRepository(db).subjects(ensured.alignment_run.id)
        assert subjects[0].status == "no_candidates"
        assert provider.requests == []


async def test_provider_failure_is_failed_not_unmatched_and_retry_rebuilds(
    session_factory,
    tmp_path,
) -> None:
    provider = FakeAlignmentProvider(
        error=AlignmentProviderError("temporary upstream failure", transient=True)
    )
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        domain = await seed_alignment_domain(db, candidate_count=1)
        request = AlignmentRequest(
            "alignment-alias",
            None,
            AlignmentConfig(max_frames=2),
        )
        service = AlignmentService(db, provider_name="fake", max_attempts=3)
        ensured = await service.ensure(domain["retrieval"].id, request)
        registry = ProcessorRegistry(
            {
                "multimodal_alignment": lambda active_db: AlignmentProcessor(
                    active_db,
                    storage,
                    FakeFrameExtractor(),
                    provider,
                )
            }
        )
        assert not await JobExecutor(db, registry).execute(ensured.job.id)
        await db.refresh(ensured.alignment_run)
        await db.refresh(ensured.job)
        failed_results, _ = await AlignmentRepository(db).results(ensured.alignment_run.id)
        assert ensured.alignment_run.status == "failed"
        assert "temporary upstream failure" in (ensured.job.error or "")
        assert "MissingGreenlet" not in (ensured.job.error or "")
        assert failed_results[0].status == "failed"
        assert (
            failed_results[0].started_at is not None and failed_results[0].completed_at is not None
        )
        assert "temporary upstream failure" in (failed_results[0].error or "")

        provider.error = None
        provider.results = [matched(36.0, 50.0, 0.88)]
        retry = await service.ensure(domain["retrieval"].id, request)
        assert retry.enqueue and not retry.created
        assert retry.alignment_run.id == ensured.alignment_run.id
        assert await JobExecutor(db, registry).execute(retry.job.id)
        rebuilt, total = await AlignmentRepository(db).results(retry.alignment_run.id)
        asset_count = await db.scalar(
            select(func.count())
            .select_from(DerivedAsset)
            .where(DerivedAsset.alignment_run_id == retry.alignment_run.id)
        )
        assert total == 1
        assert rebuilt[0].status == "matched"
        assert asset_count == 2
        assert await db.scalar(select(func.count()).select_from(AlignmentSubject)) == 1
        assert await db.scalar(select(func.count()).select_from(CandidateAlignment)) == 1


async def test_alignment_api_create_idempotency_queries_and_prerequisite(
    client,
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_alignment_domain(db)
        run_id = domain["retrieval"].id
        candidate_id = domain["candidates"][0].id

    response = await client.post(
        f"/retrieval-runs/{run_id}/alignments",
        json={"candidate_ids": [str(candidate_id)], "max_frames": 3},
    )
    assert response.status_code == 202
    payload = response.json()
    alignment_run_id = payload["alignment_run"]["id"]
    assert payload["created"] is True
    assert payload["job"]["job_type"] == "multimodal_alignment"
    assert payload["job"]["alignment_run_id"] == alignment_run_id

    repeated = await client.post(
        f"/retrieval-runs/{run_id}/alignments",
        json={"candidate_ids": [str(candidate_id)], "max_frames": 3},
    )
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["alignment_run"]["id"] == alignment_run_id

    detail = await client.get(f"/alignment-runs/{alignment_run_id}")
    results = await client.get(f"/alignment-runs/{alignment_run_id}/results?offset=0&limit=10")
    assert detail.status_code == 200
    assert detail.json()["alignment_run"]["status"] == "pending"
    assert detail.json()["subjects"] == []
    assert results.status_code == 200
    assert results.json()["total"] == 0

    missing_candidate = await client.post(
        f"/retrieval-runs/{run_id}/alignments",
        json={"candidate_ids": [str(uuid4())]},
    )
    assert missing_candidate.status_code == 409

    async with session_factory() as db:
        incomplete = await seed_alignment_domain(db, completed=False)
        incomplete_id = incomplete["retrieval"].id
    blocked = await client.post(
        f"/retrieval-runs/{incomplete_id}/alignments",
        json={},
    )
    assert blocked.status_code == 409


async def test_alignment_stale_recovery_and_attempt_limit_preserve_session(
    session_factory,
) -> None:
    from datetime import UTC, datetime, timedelta

    async with session_factory() as db:
        domain = await seed_alignment_domain(db, candidate_count=1)
        ensured = await AlignmentService(
            db,
            provider_name="fake",
            max_attempts=2,
        ).ensure(
            domain["retrieval"].id,
            AlignmentRequest(
                "alignment-alias",
                None,
                AlignmentConfig(max_frames=1),
            ),
        )
        old = datetime.now(UTC) - timedelta(minutes=10)
        ensured.job.status = "processing"
        ensured.job.attempt_count = 1
        ensured.job.heartbeat_at = old
        ensured.alignment_run.status = "processing"
        await db.commit()

        recovered = await JobExecutor(db, ProcessorRegistry()).recover(
            datetime.now(UTC) - timedelta(minutes=5)
        )
        await db.refresh(ensured.job)
        await db.refresh(ensured.alignment_run)
        assert recovered == [ensured.job.id]
        assert ensured.job.status == "pending"
        assert ensured.alignment_run.status == "pending"

        partial_subject = AlignmentSubject(
            alignment_run_id=ensured.alignment_run.id,
            subject_key=f"note:{domain['note'].id}",
            note_id=domain["note"].id,
            observation=domain["note"].raw,
            status="pending",
        )
        db.add(partial_subject)
        await db.flush()
        partial_result = CandidateAlignment(
            alignment_run_id=ensured.alignment_run.id,
            alignment_subject_id=partial_subject.id,
            retrieval_candidate_id=domain["candidates"][0].id,
            note_id=domain["note"].id,
            retrieval_rank=1,
            status="processing",
            anchors=[],
            pass_count=0,
            started_at=old,
        )
        db.add(partial_result)
        ensured.job.status = "processing"
        ensured.job.attempt_count = ensured.job.max_attempts
        ensured.job.heartbeat_at = old
        ensured.alignment_run.status = "processing"
        await db.commit()
        recovered = await JobExecutor(db, ProcessorRegistry()).recover(
            datetime.now(UTC) - timedelta(minutes=5)
        )
        await db.refresh(ensured.job)
        await db.refresh(ensured.alignment_run)
        await db.refresh(partial_subject)
        await db.refresh(partial_result)
        assert partial_subject.status == "failed"
        assert partial_result.status == "failed" and partial_result.completed_at is not None
        await db.refresh(domain["session"])
        assert recovered == []
        assert ensured.job.status == "failed"
        assert ensured.alignment_run.status == "failed"
        assert domain["session"].status == "ready"
