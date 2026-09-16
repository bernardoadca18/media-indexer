from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.repositories.alignment import AlignmentRepository
from app.repositories.domain import (
    JobRepository,
    MediaAssetRepository,
    SessionRepository,
    TemporalDerivativeRepository,
)
from app.repositories.transcription import TranscriptionRepository
from app.repositories.visual_index import VisualIndexRepository
from app.schemas.temporal_derivative import (
    TemporalDerivativeEnsureRequest,
    TemporalDerivativeProfile,
)
from app.services.alignment import AlignmentConfig, AlignmentRequest, AlignmentService
from app.services.temporal_derivative import TemporalDerivativeService, profile_fingerprint
from app.services.transcription import TranscriptionRequest, TranscriptionService
from app.services.visual_index import VisualIndexRequest, VisualIndexService


class RollbackSensitiveRecord:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.__dict__["_expired"] = False

    def expire(self) -> None:
        self.__dict__["_expired"] = True

    def __getattribute__(self, name: str):
        if name not in {"__dict__", "expire"}:
            values = object.__getattribute__(self, "__dict__")
            if values.get("_expired") and name in values:
                raise AssertionError(f"ORM field {name} was accessed after rollback")
        return object.__getattribute__(self, name)


class IntegrityRaceDatabase:
    def __init__(self, record: RollbackSensitiveRecord):
        self.record = record
        self.rolled_back = False

    def add(self, _value) -> None:
        return None

    async def execute(self, _stmt) -> None:
        return None

    async def flush(self) -> None:
        raise IntegrityError("concurrent insert", {}, RuntimeError("unique constraint"))

    async def rollback(self) -> None:
        self.record.expire()
        self.rolled_back = True


async def test_transcription_integrity_race_does_not_read_expired_session(monkeypatch) -> None:
    session = RollbackSensitiveRecord(
        id=uuid4(), project_id=uuid4(), media_id=uuid4(), status="ready"
    )
    database = IntegrityRaceDatabase(session)
    concurrent = SimpleNamespace(id=uuid4())
    concurrent_job = SimpleNamespace(id=uuid4())

    async def get_session(_repository, _session_id):
        return session

    async def by_fingerprint(_repository, _media_id, _fingerprint):
        return concurrent if database.rolled_back else None

    async def for_transcription(_repository, _transcription_id):
        return concurrent_job

    monkeypatch.setattr(SessionRepository, "get", get_session)
    monkeypatch.setattr(TranscriptionRepository, "by_fingerprint", by_fingerprint)
    monkeypatch.setattr(JobRepository, "for_transcription", for_transcription)

    result = await TranscriptionService(database, max_attempts=3).ensure(
        session.id,
        TranscriptionRequest("small", None, 5, True, True, "auto"),
    )

    assert result.transcription is concurrent
    assert result.job is concurrent_job
    assert not result.created


async def test_visual_index_integrity_race_does_not_read_expired_session(monkeypatch) -> None:
    session = RollbackSensitiveRecord(
        id=uuid4(),
        project_id=uuid4(),
        media_id=uuid4(),
        status="ready",
        media=SimpleNamespace(duration_seconds=60.0),
    )
    database = IntegrityRaceDatabase(session)
    concurrent = SimpleNamespace(id=uuid4())
    concurrent_job = SimpleNamespace(id=uuid4())

    async def get_session(_repository, _session_id):
        return session

    async def by_fingerprint(_repository, _media_id, _fingerprint):
        return concurrent if database.rolled_back else None

    async def for_visual_index(_repository, _visual_index_id):
        return concurrent_job

    monkeypatch.setattr(SessionRepository, "get", get_session)
    monkeypatch.setattr(VisualIndexRepository, "by_fingerprint", by_fingerprint)
    monkeypatch.setattr(JobRepository, "for_visual_index", for_visual_index)

    result = await VisualIndexService(database, max_attempts=3).ensure(
        session.id,
        VisualIndexRequest(True, "ffmpeg_scdet", 10.0, 10.0, 640, "jpeg", 85, 0.5),
    )

    assert result.visual_index is concurrent
    assert result.job is concurrent_job
    assert not result.created


async def test_alignment_integrity_race_does_not_read_expired_retrieval(monkeypatch) -> None:
    retrieval = RollbackSensitiveRecord(
        id=uuid4(),
        project_id=uuid4(),
        session_id=uuid4(),
        media_id=uuid4(),
        status="completed",
        configuration={"subjects": []},
    )
    database = IntegrityRaceDatabase(retrieval)
    concurrent = SimpleNamespace(id=uuid4())
    concurrent_job = SimpleNamespace(id=uuid4())

    async def get_retrieval(_repository, _retrieval_run_id):
        return retrieval

    async def candidates(_repository, _retrieval_run_id, _candidate_ids):
        return []

    async def by_fingerprint(_repository, _retrieval_run_id, _fingerprint):
        return concurrent if database.rolled_back else None

    async def for_alignment_run(_repository, _alignment_run_id):
        return concurrent_job

    monkeypatch.setattr(AlignmentRepository, "retrieval_run", get_retrieval)
    monkeypatch.setattr(AlignmentRepository, "candidates", candidates)
    monkeypatch.setattr(AlignmentRepository, "by_fingerprint", by_fingerprint)
    monkeypatch.setattr(JobRepository, "for_alignment_run", for_alignment_run)

    result = await AlignmentService(database, provider_name="fake", max_attempts=3).ensure(
        retrieval.id,
        AlignmentRequest("alignment-model", None, AlignmentConfig()),
    )

    assert result.alignment_run is concurrent
    assert result.job is concurrent_job
    assert not result.created


async def test_temporal_derivative_integrity_race_reuses_concurrent_job(monkeypatch) -> None:
    media = RollbackSensitiveRecord(
        id=uuid4(),
        project_id=uuid4(),
        sha256="a" * 64,
        media_kind="video",
        duration_seconds=60.0,
    )
    database = IntegrityRaceDatabase(media)
    concurrent = SimpleNamespace(id=uuid4())
    concurrent_job = SimpleNamespace(id=uuid4())
    derivative_profile = TemporalDerivativeProfile(
        profile_id="render_proxy_1080p30_v2",
        strategy_version="proxy_strategy_v2",
        container="mp4",
        codec="h264",
        encoder_backend="libx264",
        max_width=1920,
        max_height=1080,
        scaling_policy="fit_inside_even_dimensions",
        never_upscale=True,
        fps=30,
        pixel_format="yuv420p",
        quality_mode="crf",
        crf=20,
        preset="fast",
        gop_frames=60,
        keyframe_policy="fixed_closed_gop",
        scenecut=False,
        color_policy="bt709_passthrough",
        audio_policy="remove",
        subtitle_policy="remove",
        data_policy="remove",
        strip_metadata=True,
        movflags="faststart",
    )
    request = TemporalDerivativeEnsureRequest(
        contract_version="temporal_derivative_request_v1",
        source_sha256=media.sha256,
        source_start_microseconds=1_000_000,
        source_end_microseconds=2_000_000,
        profile_identity=profile_fingerprint(derivative_profile),
        profile=derivative_profile,
    )

    async def get_media(_repository, _media_id):
        return media

    async def by_media_and_identity(_repository, _media_id, _identity):
        return concurrent if database.rolled_back else None

    async def for_derivative(_repository, _derivative_id):
        return concurrent_job

    monkeypatch.setattr(MediaAssetRepository, "get", get_media)
    monkeypatch.setattr(TemporalDerivativeRepository, "by_media_and_identity", by_media_and_identity)
    monkeypatch.setattr(JobRepository, "for_temporal_derivative", for_derivative)

    result = await TemporalDerivativeService(database, SimpleNamespace(), 3).ensure(
        media.id, request
    )

    assert result.derivative is concurrent
    assert result.job is concurrent_job
    assert not result.created
