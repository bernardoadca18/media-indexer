from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models import MediaAsset, MediaProject, MediaSession, ProcessingJob
from app.services.jobs import JobExecutor, ProcessorRegistry


class SuccessProcessor:
    def __init__(self, calls: list[str]):
        self.calls = calls

    async def run(self, job: ProcessingJob) -> None:
        self.calls.append(job.job_type)


class FailingProcessor:
    async def run(self, job: ProcessingJob) -> None:
        raise RuntimeError("processor exploded")


def test_job_timestamps_are_timezone_aware_in_schema() -> None:
    for column_name in ("heartbeat_at", "started_at", "completed_at"):
        column = ProcessingJob.__table__.columns[column_name]
        assert column.type.timezone is True


async def make_job(
    db,
    *,
    status: str = "pending",
    job_type: str = "test",
    heartbeat_at=None,
    attempt_count: int = 0,
    max_attempts: int = 3,
) -> ProcessingJob:
    project = MediaProject(name=str(uuid4()))
    db.add(project)
    await db.flush()
    session_id, media_id = uuid4(), uuid4()
    media = MediaAsset(
        id=media_id,
        project_id=project.id,
        storage_uri=f"local://{media_id}",
        original_filename="source.mp4",
        byte_size=1,
        sha256="a" * 64,
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
    job = ProcessingJob(
        project_id=project.id,
        session_id=session_id,
        media_id=media_id,
        job_type=job_type,
        status=status,
        heartbeat_at=heartbeat_at,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )
    db.add_all([media, session, job])
    await db.commit()
    return job


async def test_dispatches_pending_job_by_type(session_factory) -> None:
    calls: list[str] = []
    registry = ProcessorRegistry({"test": lambda db: SuccessProcessor(calls)})
    async with session_factory() as db:
        job = await make_job(db)
        assert await JobExecutor(db, registry).execute(job.id)
        await db.refresh(job)
        assert calls == ["test"]
        assert job.status == "completed"
        assert job.attempt_count == 1


async def test_unknown_processor_fails_job(session_factory) -> None:
    async with session_factory() as db:
        job = await make_job(db, job_type="unknown")
        assert not await JobExecutor(db, ProcessorRegistry()).execute(job.id)
        await db.refresh(job)
        assert job.status == "failed"
        assert "unknown job type" in (job.error or "")


async def test_processor_failure_is_persisted(session_factory) -> None:
    registry = ProcessorRegistry({"test": lambda db: FailingProcessor()})
    async with session_factory() as db:
        job = await make_job(db)
        assert not await JobExecutor(db, registry).execute(job.id)
        await db.refresh(job)
        assert job.status == "failed"
        assert job.error == "processor exploded"


async def test_heartbeat_updates_active_job(session_factory) -> None:
    async with session_factory() as db:
        old = datetime.now(UTC) - timedelta(minutes=10)
        job = await make_job(db, status="processing", heartbeat_at=old)
        assert await JobExecutor(db, ProcessorRegistry()).heartbeat(job.id)
        await db.refresh(job)
        assert job.heartbeat_at is not None
        assert job.heartbeat_at.replace(tzinfo=UTC) > old


async def test_recovery_handles_pending_stale_and_live_jobs(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as db:
        pending = await make_job(db)
        stale = await make_job(
            db, status="processing", heartbeat_at=now - timedelta(minutes=10), attempt_count=1
        )
        live = await make_job(db, status="processing", heartbeat_at=now, attempt_count=1)
        recovered = await JobExecutor(db, ProcessorRegistry()).recover(now - timedelta(minutes=5))
        assert set(recovered) == {pending.id, stale.id}
        await db.refresh(stale)
        await db.refresh(live)
        assert stale.status == "pending"
        assert live.status == "processing"


async def test_stale_job_at_attempt_limit_fails_definitively(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as db:
        job = await make_job(
            db,
            status="processing",
            heartbeat_at=now - timedelta(minutes=10),
            attempt_count=3,
            max_attempts=3,
        )
        recovered = await JobExecutor(db, ProcessorRegistry()).recover(now - timedelta(minutes=5))
        await db.refresh(job)
        assert job.id not in recovered
        assert job.status == "failed"
        assert "attempt limit" in (job.error or "")
