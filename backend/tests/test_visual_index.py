import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.models import MediaAsset, MediaProject, MediaSession
from app.repositories.visual_index import VisualIndexRepository
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.storage import LocalStorage
from app.services.visual_index import (
    VisualIndexProcessor,
    VisualIndexRequest,
    VisualIndexService,
    representative_candidates,
)
from app.services.visual_tools import ExtractedFrame, SceneResult


class FakeSceneDetector:
    name = "ffmpeg_scdet"

    async def detect(
        self, source: Path, duration_seconds: float, threshold: float
    ) -> list[SceneResult]:
        assert source.read_bytes() == b"video"
        assert duration_seconds == 30.0
        assert threshold == 10.0
        return [
            SceneResult(0.0, 10.0, 18.0, {"cut": "opening"}),
            SceneResult(10.0, 30.0, 24.0, {"cut": "gameplay"}),
        ]


class FakeFrameExtractor:
    name = "ffmpeg"

    def __init__(self, fail_after: int | None = None):
        self.calls = 0
        self.fail_after = fail_after

    async def extract(
        self,
        source: Path,
        timestamp_seconds: float,
        width: int,
        image_format: str,
        jpeg_quality: int,
    ) -> ExtractedFrame:
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("frame extraction interrupted")
        self.calls += 1
        assert source.read_bytes() == b"video"
        assert width == 640
        assert image_format == "jpeg"
        assert jpeg_quality == 85
        return ExtractedFrame(
            content=f"jpeg:{timestamp_seconds:.3f}".encode(),
            mime_type="image/jpeg",
            width=640,
            height=360,
        )


async def ready_session(db, storage: LocalStorage) -> MediaSession:
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
        duration_seconds=30.0,
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


def request(*, width: int = 640) -> VisualIndexRequest:
    return VisualIndexRequest(
        scene_detection_enabled=True,
        scene_detector="ffmpeg_scdet",
        scene_threshold=10.0,
        periodic_frame_interval_seconds=10.0,
        frame_width=width,
        image_format="jpeg",
        jpeg_quality=85,
        dedup_tolerance_seconds=0.5,
    )


def registry(storage: LocalStorage, extractor: FakeFrameExtractor) -> ProcessorRegistry:
    return ProcessorRegistry(
        {
            "visual_index": lambda db: VisualIndexProcessor(
                db, storage, FakeSceneDetector(), extractor, batch_size=1
            )
        }
    )


def test_representative_candidates_prioritize_scene_midpoints_and_merge_origins() -> None:
    candidates = representative_candidates(
        30.0,
        10.0,
        [SceneResult(0.0, 10.0), SceneResult(10.0, 30.0)],
        0.5,
    )
    assert [item.timestamp_seconds for item in candidates] == [0.0, 5.0, 10.0, 20.0]
    merged = candidates[-1]
    assert merged.origins == {"periodic", "scene"}
    assert merged.scene_sequences == [2]
    assert (merged.source_start_seconds, merged.source_end_seconds) == (10.0, 30.0)


async def test_visual_index_persists_scenes_frames_and_metadata(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        result = await VisualIndexService(db, max_attempts=3).ensure(session.id, request())

        assert result.created and result.enqueue
        assert result.job.job_type == "visual_index"
        assert result.job.visual_index_id == result.visual_index.id
        assert await JobExecutor(db, registry(storage, FakeFrameExtractor())).execute(result.job.id)

        await db.refresh(result.visual_index)
        await db.refresh(result.job)
        await db.refresh(session)
        scenes, scene_total = await VisualIndexRepository(db).scenes(result.visual_index.id, 0, 100)
        frames, frame_total = await VisualIndexRepository(db).frames(result.visual_index.id, 0, 100)

        assert result.visual_index.status == "completed"
        assert result.job.status == "completed"
        assert result.job.attempt_count == 1
        assert session.status == "ready"
        assert scene_total == 2
        assert [item.sequence for item in scenes] == [1, 2]
        assert [(item.start_seconds, item.end_seconds) for item in scenes] == [
            (0.0, 10.0),
            (10.0, 30.0),
        ]
        assert frame_total == 4
        assert [item.timestamp_seconds for item in frames] == [0.0, 5.0, 10.0, 20.0]
        merged = frames[-1]
        assert merged.asset_metadata["origins"] == ["periodic", "scene"]
        assert (merged.width, merged.height, merged.mime_type) == (640, 360, "image/jpeg")
        content = storage.resolve(merged.storage_uri).read_bytes()
        assert merged.byte_size == len(content)
        assert merged.sha256 == hashlib.sha256(content).hexdigest()


async def test_visual_index_is_idempotent_and_changed_config_is_distinct(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        service = VisualIndexService(db, max_attempts=3)
        first = await service.ensure(session.id, request())
        same = await service.ensure(session.id, request())
        different = await service.ensure(session.id, request(width=768))

        assert same.visual_index.id == first.visual_index.id
        assert same.job.id == first.job.id
        assert not same.created and not same.enqueue
        assert different.visual_index.id != first.visual_index.id
        assert different.visual_index.configuration_fingerprint != (
            first.visual_index.configuration_fingerprint
        )


async def test_failure_cleans_partial_outputs_and_retry_does_not_duplicate(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        result = await VisualIndexService(db, max_attempts=3).ensure(session.id, request())
        assert not await JobExecutor(
            db, registry(storage, FakeFrameExtractor(fail_after=2))
        ).execute(result.job.id)

        await db.refresh(result.visual_index)
        await db.refresh(result.job)
        await db.refresh(session)
        _, scene_total = await VisualIndexRepository(db).scenes(result.visual_index.id, 0, 100)
        _, frame_total = await VisualIndexRepository(db).frames(result.visual_index.id, 0, 100)
        namespace = (
            tmp_path
            / "projects"
            / str(session.project_id)
            / "sessions"
            / str(session.id)
            / "derived"
            / "visual"
            / str(result.visual_index.id)
        )
        assert result.visual_index.status == "failed"
        assert result.job.status == "failed"
        assert session.status == "ready"
        assert (scene_total, frame_total) == (0, 0)
        assert not namespace.exists()

        retry = await VisualIndexService(db, max_attempts=3).ensure(session.id, request())
        assert retry.enqueue and not retry.created
        assert await JobExecutor(db, registry(storage, FakeFrameExtractor())).execute(retry.job.id)
        _, scene_total = await VisualIndexRepository(db).scenes(retry.visual_index.id, 0, 100)
        frames, frame_total = await VisualIndexRepository(db).frames(retry.visual_index.id, 0, 100)
        assert retry.job.attempt_count == 2
        assert (scene_total, frame_total) == (2, 4)
        assert len({item.storage_uri for item in frames}) == 4


async def test_stale_visual_job_is_recovered_without_demoting_session(
    session_factory, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        session = await ready_session(db, storage)
        result = await VisualIndexService(db, max_attempts=3).ensure(session.id, request())
        result.job.status = "processing"
        result.job.attempt_count = 1
        result.job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        result.visual_index.status = "processing"
        await db.commit()

        recovered = await JobExecutor(db, ProcessorRegistry()).recover(
            datetime.now(UTC) - timedelta(minutes=5)
        )
        await db.refresh(result.job)
        await db.refresh(result.visual_index)
        await db.refresh(session)
        assert recovered == [result.job.id]
        assert result.job.status == "pending"
        assert result.job.stage == "recovered"
        assert result.visual_index.status == "pending"
        assert session.status == "ready"
