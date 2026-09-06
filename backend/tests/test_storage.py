import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.storage import LocalStorage


class ChunkedUpload:
    filename = r"C:\capture\gameplay.mp4"
    content_type = "video/mp4"

    def __init__(self, chunks: list[bytes], *, fail_at: int | None = None):
        self.chunks = chunks
        self.index = 0
        self.fail_at = fail_at

    async def read(self, size: int = -1) -> bytes:
        assert size == 1024 * 1024
        if self.fail_at is not None and self.index == self.fail_at:
            raise OSError("upload interrupted")
        if self.index >= len(self.chunks):
            return b""
        chunk = self.chunks[self.index]
        self.index += 1
        return chunk


async def test_large_upload_streams_chunks_and_preserves_hash_and_size(
    tmp_path: Path,
) -> None:
    chunks = [b"a" * 700_000, b"b" * 700_000, b"c" * 700_000]
    upload = ChunkedUpload(chunks)
    storage = LocalStorage(tmp_path)
    stored = await storage.save_source(uuid4(), uuid4(), upload)  # type: ignore[arg-type]

    assert stored.byte_size == 2_100_000
    assert stored.sha256 == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert stored.uri.endswith("/source/gameplay.mp4")
    assert storage.resolve(stored.uri).read_bytes() == b"".join(chunks)


async def test_failed_upload_removes_partial_file(tmp_path: Path) -> None:
    project_id, session_id = uuid4(), uuid4()
    storage = LocalStorage(tmp_path)
    upload = ChunkedUpload([b"first", b"second"], fail_at=1)

    with pytest.raises(OSError, match="upload interrupted"):
        await storage.save_source(  # type: ignore[arg-type]
            project_id, session_id, upload
        )

    source = tmp_path / "projects" / str(project_id) / "sessions" / str(session_id) / "source"
    assert list(source.iterdir()) == []


def test_storage_rejects_escaping_uri(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValueError, match="escapes root"):
        storage.resolve("local://../../outside")


async def test_hdd_storage_saves_to_secondary_location(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()

    chunks = [b"x" * 500_000, b"y" * 500_000]
    upload = ChunkedUpload(chunks)
    storage = LocalStorage(primary, root_hdd=hdd)

    project_id, session_id = uuid4(), uuid4()
    stored = await storage.save_source_hdd(project_id, session_id, upload)  # type: ignore[arg-type]

    assert stored.byte_size == 1_000_000
    assert stored.sha256 == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert stored.uri.startswith("local-hdd://")
    assert storage.resolve_hdd(stored.uri).read_bytes() == b"".join(chunks)

    # Verify file is in HDD location, not primary
    primary_source = primary / "projects" / str(project_id) / "sessions" / str(session_id) / "source"
    hdd_source = hdd / "projects" / str(project_id) / "sessions" / str(session_id) / "source"
    assert not primary_source.exists()
    assert hdd_source.exists()


async def test_hdd_storage_read_bytes(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()

    chunks = [b"test content for hdd"]
    upload = ChunkedUpload(chunks)
    storage = LocalStorage(primary, root_hdd=hdd)

    project_id, session_id = uuid4(), uuid4()
    stored = await storage.save_source_hdd(project_id, session_id, upload)  # type: ignore[arg-type]

    read_bytes = await storage.read_bytes_hdd(stored.uri)
    assert read_bytes == b"".join(chunks)


def test_hdd_storage_rejects_escaping_uri(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()

    storage = LocalStorage(primary, root_hdd=hdd)
    with pytest.raises(ValueError, match="escapes root"):
        storage.resolve_hdd("local-hdd://../../outside")


async def test_hdd_storage_falls_back_to_primary_when_not_configured(tmp_path: Path) -> None:
    """Test that save_source_hdd falls back to primary storage when HDD not configured."""
    storage = LocalStorage(tmp_path, root_hdd=None)

    chunks = [b"fallback test"]
    upload = ChunkedUpload(chunks)
    project_id, session_id = uuid4(), uuid4()

    # save_source_hdd should fall back to primary when HDD not configured
    stored = await storage.save_source_hdd(project_id, session_id, upload)  # type: ignore[arg-type]

    # Should use primary storage (local://), not HDD (local-hdd://)
    assert stored.uri.startswith("local://")
    assert not stored.uri.startswith("local-hdd://")
    assert storage.resolve(stored.uri).read_bytes() == b"fallback test"
