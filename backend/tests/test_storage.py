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
