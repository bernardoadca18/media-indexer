import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.models import MediaAsset, MediaProject
from app.schemas.temporal_derivative import (
    TemporalDerivativeEnsureRequest,
    TemporalDerivativeProfile,
)
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.probe import FFprobe
from app.services.storage import LocalStorage
from app.services.temporal_derivative import (
    TemporalDerivativeConflictError,
    TemporalDerivativeProcessor,
    TemporalDerivativeService,
    derivative_identity,
    profile_fingerprint,
)


def profile(*, max_width: int = 1920) -> TemporalDerivativeProfile:
    return TemporalDerivativeProfile(
        profile_id="render_proxy_1080p30_v2",
        strategy_version="proxy_strategy_v2",
        container="mp4",
        codec="h264",
        encoder_backend="libx264",
        max_width=max_width,
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


def request(
    source_sha256: str,
    *,
    start: int = 250_001,
    end: int = 1_750_019,
    derivative_profile: TemporalDerivativeProfile | None = None,
) -> TemporalDerivativeEnsureRequest:
    selected = derivative_profile or profile()
    return TemporalDerivativeEnsureRequest(
        contract_version="temporal_derivative_request_v1",
        source_sha256=source_sha256,
        source_start_microseconds=start,
        source_end_microseconds=end,
        profile_identity=profile_fingerprint(selected),
        profile=selected,
    )


def generate_source(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=3",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )


async def add_media(db, root: Path, suffix: str = ".mp4") -> MediaAsset:
    source = root / f"source{suffix}"
    generate_source(source)
    project = MediaProject(name=str(uuid4()))
    db.add(project)
    await db.flush()
    media = MediaAsset(
        project_id=project.id,
        storage_uri=f"local://{source.name}",
        original_filename=source.name,
        content_type="video/x-matroska" if suffix == ".mkv" else "video/mp4",
        byte_size=source.stat().st_size,
        sha256=_sha256(source),
        media_kind="video",
        duration_seconds=3.0,
        format_name="matroska,webm" if suffix == ".mkv" else "mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        audio_codec=None,
        fps=30,
        width=320,
        height=180,
    )
    db.add(media)
    await db.commit()
    return media


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def test_derivative_identity_is_deterministic_and_covers_immutable_inputs() -> None:
    selected = profile()
    identity = derivative_identity(
        "a" * 64, 1, 2, profile_fingerprint(selected), "proxy_strategy_v2"
    )
    assert identity == derivative_identity(
        "a" * 64, 1, 2, profile_fingerprint(selected), "proxy_strategy_v2"
    )
    assert identity != derivative_identity(
        "b" * 64, 1, 2, profile_fingerprint(selected), "proxy_strategy_v2"
    )
    assert identity != derivative_identity(
        "a" * 64, 1, 3, profile_fingerprint(selected), "proxy_strategy_v2"
    )
    assert identity != derivative_identity(
        "a" * 64, 1, 2, profile_fingerprint(profile(max_width=1280)), "proxy_strategy_v2"
    )


async def test_ensure_reuses_same_request_and_rejects_invalid_bounds(db, tmp_path: Path) -> None:
    media = await add_media(db, tmp_path)
    storage = LocalStorage(tmp_path)
    service = TemporalDerivativeService(db, storage, 3)
    first = await service.ensure(media.id, request(media.sha256))
    repeated = await service.ensure(media.id, request(media.sha256))

    assert first.created
    assert repeated.derivative.id == first.derivative.id
    assert repeated.job.id == first.job.id
    assert not repeated.created
    with pytest.raises(TemporalDerivativeConflictError, match="increasing"):
        await service.ensure(media.id, request(media.sha256, start=1_000_000, end=1_000_000))
    with pytest.raises(TemporalDerivativeConflictError, match="exceeds"):
        await service.ensure(media.id, request(media.sha256, start=0, end=3_000_001))


@pytest.mark.parametrize("suffix", [".mp4", ".mkv"])
async def test_processor_generates_valid_atomic_derivative_without_read_bytes(
    session_factory, tmp_path: Path, monkeypatch, suffix: str
) -> None:
    async with session_factory() as db:
        media = await add_media(db, tmp_path, suffix)
        storage = LocalStorage(tmp_path)
        ensured = await TemporalDerivativeService(db, storage, 3).ensure(
            media.id, request(media.sha256)
        )
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda _path: (_ for _ in ()).throw(AssertionError("large derivative read into RAM")),
        )
        registry = ProcessorRegistry(
            {
                "temporal_derivative": lambda active_db: TemporalDerivativeProcessor(
                    active_db, storage, "ffmpeg", FFprobe("ffprobe")
                )
            }
        )
        assert await JobExecutor(db, registry).execute(ensured.job.id)
        await db.refresh(ensured.derivative)
        await db.refresh(ensured.job)

        assert ensured.derivative.status == "completed"
        assert ensured.job.status == "completed"
        assert ensured.derivative.sha256 == _sha256(
            storage.resolve_any(ensured.derivative.storage_uri or "")
        )
        assert (
            ensured.derivative.byte_size
            == storage.resolve_any(ensured.derivative.storage_uri or "").stat().st_size
        )
        assert ensured.derivative.codec == "h264"
        assert ensured.derivative.width == 320
        assert ensured.derivative.height == 180
        assert ensured.derivative.fps == pytest.approx(30)
        assert not list(tmp_path.rglob("*.tmp"))


async def test_partial_ffmpeg_failure_never_promotes_final_file(db, tmp_path: Path) -> None:
    media = await add_media(db, tmp_path)
    storage = LocalStorage(tmp_path)
    ensured = await TemporalDerivativeService(db, storage, 3).ensure(
        media.id, request(media.sha256)
    )
    failing = tmp_path / "partial-ffmpeg"
    failing.write_text('#!/bin/sh\nfor last do :; done\nprintf partial > "$last"\nexit 1\n')
    failing.chmod(0o755)
    processor = TemporalDerivativeProcessor(db, storage, str(failing), FFprobe("ffprobe"))

    with pytest.raises(RuntimeError):
        await processor.run(ensured.job)
    derivative_root = tmp_path / "projects" / str(media.project_id) / "media" / str(media.id)
    assert not list(derivative_root.rglob("proxy.mp4"))
    assert not list(derivative_root.rglob("*.tmp"))


async def test_retry_after_restart_reuses_promoted_valid_derivative(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory() as db:
        media = await add_media(db, tmp_path)
        storage = LocalStorage(tmp_path)
        ensured = await TemporalDerivativeService(db, storage, 3).ensure(
            media.id, request(media.sha256)
        )
        processor = TemporalDerivativeProcessor(db, storage, "ffmpeg", FFprobe("ffprobe"))
        await processor.run(ensured.job)
        await db.refresh(ensured.derivative)
        original_sha256 = ensured.derivative.sha256
        ensured.derivative.status = "pending"
        ensured.derivative.storage_uri = None
        ensured.derivative.sha256 = None
        ensured.derivative.byte_size = None
        ensured.derivative.duration_seconds = None
        ensured.derivative.width = None
        ensured.derivative.height = None
        ensured.derivative.fps = None
        ensured.derivative.codec = None
        await db.commit()

        restarted = TemporalDerivativeProcessor(db, storage, "/bin/false", FFprobe("ffprobe"))
        await restarted.run(ensured.job)
        await db.refresh(ensured.derivative)
        assert ensured.derivative.status == "completed"
        assert ensured.derivative.sha256 == original_sha256


async def test_completed_derivative_metadata_and_content_are_immutable_contracts(
    client, session_factory, tmp_path: Path
) -> None:
    async with session_factory() as db:
        media = await add_media(db, tmp_path)
        storage = LocalStorage(tmp_path)
        ensured = await TemporalDerivativeService(db, storage, 3).ensure(
            media.id, request(media.sha256)
        )
        await TemporalDerivativeProcessor(
            db, storage, "ffmpeg", FFprobe("ffprobe")
        ).run(ensured.job)
        derivative_id = ensured.derivative.id
        expected_sha256 = ensured.derivative.sha256
        expected_size = ensured.derivative.byte_size

    metadata = await client.get(f"/temporal-derivatives/{derivative_id}")
    assert metadata.status_code == 200
    assert metadata.json()["sha256"] == expected_sha256
    assert metadata.json()["byte_size"] == expected_size
    assert metadata.json()["content_url"] == (
        f"/temporal-derivatives/{derivative_id}/content"
    )

    content = await client.get(f"/temporal-derivatives/{derivative_id}/content")
    assert content.status_code == 200
    assert content.headers["cache-control"] == "public, immutable, max-age=31536000"
    assert content.headers["x-content-sha256"] == expected_sha256
    assert len(content.content) == expected_size


async def test_cross_media_same_sha_allows_independent_derivatives(
    session_factory, tmp_path: Path
) -> None:
    """Two MediaAssets with identical SHA must have independent derivatives."""
    async with session_factory() as db:
        # Create first media asset (which creates its own project)
        media_a = await add_media(db, tmp_path)
        storage = LocalStorage(tmp_path)

        # Create second media with identical SHA by copying the source file
        source_a = tmp_path / "source.mp4"
        source_b = tmp_path / "source_b.mp4"
        if not source_a.exists():
            generate_source(source_a)
        import shutil
        shutil.copy(source_a, source_b)

        sha = media_a.sha256
        project = MediaProject(name=str(uuid4()))
        db.add(project)
        await db.flush()
        media_b = MediaAsset(
            project_id=project.id,
            storage_uri=f"local://{source_b.name}",
            original_filename=source_b.name,
            content_type="video/mp4",
            byte_size=source_b.stat().st_size,
            sha256=sha,
            media_kind="video",
            duration_seconds=3.0,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            audio_codec=None,
            fps=30,
            width=320,
            height=180,
        )
        db.add(media_b)
        await db.commit()

        assert media_a.sha256 == media_b.sha256
        assert media_a.id != media_b.id

        service = TemporalDerivativeService(db, storage, 3)

        # Ensure derivative for media_a
        result_a = await service.ensure(media_a.id, request(sha))
        processor = TemporalDerivativeProcessor(db, storage, "ffmpeg", FFprobe("ffprobe"))
        await processor.run(result_a.job)
        await db.refresh(result_a.derivative)

        # Ensure derivative for media_b with same SHA/window
        result_b = await service.ensure(media_b.id, request(sha))

        # Must be different derivatives (independent by media_id)
        assert result_a.derivative.id != result_b.derivative.id
        assert result_a.derivative.source_media_id == media_a.id
        assert result_b.derivative.source_media_id == media_b.id

        # Both should have same content-addressed identity
        assert result_a.derivative.identity == result_b.derivative.identity


async def test_corrupted_completed_derivative_is_detected_and_regenerated(
    session_factory, tmp_path: Path
) -> None:
    """A derivative with matching size but wrong SHA must be regenerated."""
    async with session_factory() as db:
        media = await add_media(db, tmp_path)
        storage = LocalStorage(tmp_path)
        service = TemporalDerivativeService(db, storage, 3)

        result = await service.ensure(media.id, request(media.sha256))
        processor = TemporalDerivativeProcessor(db, storage, "ffmpeg", FFprobe("ffprobe"))
        await processor.run(result.job)
        await db.refresh(result.derivative)

        assert result.derivative.status == "completed"
        original_sha = result.derivative.sha256
        storage_uri = result.derivative.storage_uri
        assert storage_uri is not None

        # Corrupt the file (same size, wrong content)
        derivative_path = storage.resolve_any(storage_uri)
        original_size = derivative_path.stat().st_size
        corrupt_content = b"\xDE\xAD\xBE\xEF" * (original_size // 4)
        corrupt_content = corrupt_content[:original_size]
        derivative_path.write_bytes(corrupt_content)

        # Reset derivative to pending to simulate re-ensure
        result.derivative.status = "pending"
        await db.commit()

        # Re-ensure should detect corruption and regenerate
        result2 = await service.ensure(media.id, request(media.sha256))
        await processor.run(result2.job)
        await db.refresh(result2.derivative)

        assert result2.derivative.status == "completed"
        assert result2.derivative.sha256 == original_sha

        # Verify the file now has correct content
        actual_sha = hashlib.sha256(derivative_path.read_bytes()).hexdigest()
        assert actual_sha == original_sha
