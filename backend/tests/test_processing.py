from pathlib import Path
from uuid import uuid4

from app.models import MediaAsset, MediaProject, MediaSession, ProcessingJob
from app.services.probe import ProbeResult
from app.services.processing import InitialIndexProcessor
from app.services.storage import LocalStorage


class FakeProbe:
    async def inspect(self, path: Path) -> ProbeResult:
        assert path.read_bytes() == b"video"
        return ProbeResult(42.0, "mp4", "h264", "aac", 60.0, 1920, 1080, {"ok": True})


async def test_initial_processing_persists_metadata(session_factory, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    async with session_factory() as db:
        project = MediaProject(name="game")
        db.add(project)
        await db.flush()
        session = MediaSession(
            id=uuid4(),
            project_id=project.id,
            media_id=uuid4(),
            label="001",
            notes_filename="001.txt",
            raw_notes="raw",
            status="pending",
        )
        media = MediaAsset(
            id=session.media_id,
            project_id=project.id,
            storage_uri=f"local://projects/{project.id}/sessions/{session.id}/source/001.mp4",
            original_filename="001.mp4",
            content_type="video/mp4",
            byte_size=5,
            sha256="x" * 64,
        )
        path = storage.resolve(media.storage_uri)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"video")
        db.add_all([media, session])
        job = ProcessingJob(project_id=project.id, session_id=session.id, media_id=media.id)
        db.add(job)
        await db.commit()

        await InitialIndexProcessor(db, storage, FakeProbe()).run(job.id)
        await db.refresh(job)
        await db.refresh(media)
        await db.refresh(session)
        assert job.status == "completed"
        assert session.status == "ready"
        assert media.duration_seconds == 42.0
        assert media.probe_metadata == {"ok": True}
