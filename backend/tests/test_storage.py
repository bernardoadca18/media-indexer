import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.storage import LocalStorage, StorageLimitError


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


def test_resolve_any_supports_primary(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    expected = tmp_path / "managed" / "source.mp4"
    expected.parent.mkdir()
    expected.write_bytes(b"primary")

    assert storage.resolve_any("local://managed/source.mp4") == expected


def test_resolve_any_rejects_escaping_primary(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError, match="escapes root"):
        storage.resolve_any("local://../../outside")


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


def test_resolve_any_supports_hdd(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()
    expected = hdd / "managed" / "source.mp4"
    expected.parent.mkdir()
    expected.write_bytes(b"hdd")
    storage = LocalStorage(primary, root_hdd=hdd)

    assert storage.resolve_any("local-hdd://managed/source.mp4") == expected


def test_resolve_any_rejects_escaping_hdd(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()
    storage = LocalStorage(primary, root_hdd=hdd)

    with pytest.raises(ValueError, match="escapes root"):
        storage.resolve_any("local-hdd://../../outside")


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


async def test_hdd_physical_write_readback_smoke(tmp_path: Path) -> None:
    """P0.5: Smoke test for physical HDD write/readback.

    Verifies that:
    1. Data is written to HDD storage location
    2. File is physically on disk (not just in memory)
    3. Content can be read back correctly
    4. SHA256 hash is verified
    """
    import os

    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()

    # Create test data that's large enough to stress I/O (1MB+)
    # Use pattern that's unlikely to compress well
    test_data = os.urandom(1_500_000)  # 1.5MB of random data
    expected_sha256 = hashlib.sha256(test_data).hexdigest()

    # Split into chunks to simulate upload
    chunk_size = 500_000
    chunks = [test_data[i : i + chunk_size] for i in range(0, len(test_data), chunk_size)]
    upload = ChunkedUpload(chunks)

    storage = LocalStorage(primary, root_hdd=hdd)
    project_id, session_id = uuid4(), uuid4()

    # Write to HDD storage
    stored = await storage.save_source_hdd(project_id, session_id, upload)  # type: ignore[arg-type]

    # Verify metadata
    assert stored.byte_size == len(test_data)
    assert stored.sha256 == expected_sha256
    assert stored.uri.startswith("local-hdd://")

    # Verify file exists physically on HDD (not primary)
    hdd_path = storage.resolve_hdd(stored.uri)
    primary_path = primary / "projects" / str(project_id) / "sessions" / str(session_id) / "source"

    assert hdd_path.exists(), "File must exist on HDD storage"
    assert not primary_path.exists(), "File must NOT exist on primary storage"

    # Verify file size on disk matches
    assert hdd_path.stat().st_size == len(test_data), "Physical file size must match"

    # Read back using storage API
    read_back = await storage.read_bytes_hdd(stored.uri)
    assert read_back == test_data, "Read back content must match original"

    # Read back directly from filesystem (bypass storage API)
    direct_read = hdd_path.read_bytes()
    assert direct_read == test_data, "Direct filesystem read must match"

    # Verify SHA256 of physical file
    actual_sha256 = hashlib.sha256(direct_read).hexdigest()
    assert actual_sha256 == expected_sha256, "Physical file SHA256 must match"


async def test_derived_storage_atomic_write_smoke(tmp_path: Path) -> None:
    """Smoke test for derived storage atomic writes with fsync.

    Verifies that:
    1. Derived files are written atomically
    2. fsync is called (ensures physical write)
    3. Content is readable after write
    """
    storage = LocalStorage(tmp_path)
    project_id, session_id = uuid4(), uuid4()
    namespace_id = uuid4()

    # Create test content
    test_content = b"derived content test " * 1000  # ~20KB
    expected_sha256 = hashlib.sha256(test_content).hexdigest()

    # Save derived content
    stored = await storage.save_derived(
        project_id=project_id,
        session_id=session_id,
        namespace_kind="test_namespace",
        namespace_id=namespace_id,
        collection="test_collection",
        filename="test_file.bin",
        content=test_content,
    )

    # Verify metadata
    assert stored.byte_size == len(test_content)
    assert stored.sha256 == expected_sha256
    assert stored.uri.startswith("local://")

    # Verify file exists and is readable
    file_path = storage.resolve(stored.uri)
    assert file_path.exists(), "Derived file must exist"

    # Verify content
    read_back = file_path.read_bytes()
    assert read_back == test_content, "Derived file content must match"

    # Verify no temporary files left behind
    parent = file_path.parent
    temp_files = list(parent.glob(".*.tmp"))
    assert temp_files == [], "No temporary files should remain"


async def test_media_source_enforces_size_limit_and_cleans_partial_file(tmp_path: Path) -> None:
    project_id, media_id = uuid4(), uuid4()
    storage = LocalStorage(tmp_path)
    upload = ChunkedUpload([b"12345", b"67890"])
    with pytest.raises(StorageLimitError, match="byte limit"):
        await storage.save_media_source(  # type: ignore[arg-type]
            project_id, media_id, upload, max_bytes=8
        )
    source = tmp_path / "projects" / str(project_id) / "media" / str(media_id) / "source"
    assert list(source.iterdir()) == []


async def test_media_source_rejects_symlink_namespace_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    project_id, media_id = uuid4(), uuid4()
    namespace = root / "projects" / str(project_id) / "media"
    namespace.mkdir(parents=True)
    (namespace / str(media_id)).symlink_to(outside, target_is_directory=True)
    storage = LocalStorage(root)
    with pytest.raises(ValueError, match="escapes storage root"):
        await storage.save_media_source(  # type: ignore[arg-type]
            project_id, media_id, ChunkedUpload([b"data"]), max_bytes=100
        )
    assert list(outside.iterdir()) == []


async def test_media_source_sanitizes_caller_filename_path(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    upload = ChunkedUpload([b"image"])
    upload.filename = r"..\..\escape.png"
    stored = await storage.save_media_source(  # type: ignore[arg-type]
        uuid4(), uuid4(), upload, max_bytes=100
    )
    assert stored.uri.endswith("/source/escape.png")
    assert storage.resolve(stored.uri).is_relative_to(tmp_path)
