from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.repositories.alignment import AlignmentRepository
from app.repositories.domain import JobRepository, SessionRepository
from app.repositories.transcription import TranscriptionRepository
from app.repositories.visual_index import VisualIndexRepository
from app.services.alignment import AlignmentConfig, AlignmentRequest, AlignmentService
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
