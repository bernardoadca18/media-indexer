import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol
from uuid import UUID

from fastapi import UploadFile


@dataclass(frozen=True)
class StoredObject:
    uri: str
    byte_size: int
    sha256: str


class Storage(Protocol):
    async def save_source(
        self, project_id: UUID, session_id: UUID, upload: UploadFile
    ) -> StoredObject: ...
    def resolve(self, uri: str) -> Path: ...
    async def delete(self, uri: str) -> None: ...


def _write_chunk(stream: BinaryIO, digest: Any, chunk: bytes) -> int:
    stream.write(chunk)
    digest.update(chunk)
    return len(chunk)


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    async def save_source(
        self, project_id: UUID, session_id: UUID, upload: UploadFile
    ) -> StoredObject:
        supplied = (upload.filename or "source.bin").replace("\\", "/")
        filename = supplied.rsplit("/", 1)[-1] or "source.bin"
        if filename in {".", ".."}:
            filename = "source.bin"
        relative = PurePosixPath(
            "projects", str(project_id), "sessions", str(session_id), "source", filename
        )
        target = self.root.joinpath(*relative.parts)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        stream = await asyncio.to_thread(target.open, "wb")
        try:
            while chunk := await upload.read(1024 * 1024):
                size += await asyncio.to_thread(_write_chunk, stream, digest, chunk)
        except Exception:
            await asyncio.to_thread(stream.close)
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        else:
            await asyncio.to_thread(stream.close)
        return StoredObject(f"local://{relative.as_posix()}", size, digest.hexdigest())

    def resolve(self, uri: str) -> Path:
        if not uri.startswith("local://"):
            raise ValueError("unsupported storage URI")
        candidate = self.root.joinpath(*PurePosixPath(uri[8:]).parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("storage URI escapes root")
        return candidate

    async def delete(self, uri: str) -> None:
        path = self.resolve(uri)
        await asyncio.to_thread(path.unlink, missing_ok=True)
