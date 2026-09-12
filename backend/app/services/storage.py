import asyncio
import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4

from fastapi import UploadFile


@dataclass(frozen=True)
class StoredObject:
    uri: str
    byte_size: int
    sha256: str


class StorageLimitError(ValueError):
    pass


class Storage(Protocol):
    async def save_source(
        self, project_id: UUID, session_id: UUID, upload: UploadFile
    ) -> StoredObject: ...
    async def save_source_hdd(
        self, project_id: UUID, session_id: UUID, upload: UploadFile
    ) -> StoredObject: ...
    async def save_media_source(
        self, project_id: UUID, media_id: UUID, upload: UploadFile, max_bytes: int
    ) -> StoredObject: ...
    async def save_derived(
        self,
        project_id: UUID,
        session_id: UUID,
        namespace_kind: str,
        namespace_id: UUID,
        collection: str,
        filename: str,
        content: bytes,
    ) -> StoredObject: ...
    async def delete_derived_namespace(
        self,
        project_id: UUID,
        session_id: UUID,
        namespace_kind: str,
        namespace_id: UUID,
    ) -> None: ...
    def resolve(self, uri: str) -> Path: ...
    def resolve_any(self, uri: str) -> Path: ...
    async def read_bytes_hdd(self, uri: str) -> bytes: ...
    async def delete(self, uri: str) -> None: ...


def _write_chunk(stream: BinaryIO, digest: Any, chunk: bytes) -> int:
    stream.write(chunk)
    digest.update(chunk)
    return len(chunk)


def _safe_component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value in {".", ".."}:
        raise ValueError("unsafe storage path component")
    return value


def _write_atomic(target: Path, content: bytes) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _delete_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _ensure_managed_parent(root: Path, relative_parent: PurePosixPath) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for part in relative_parent.parts:
        candidate = current / part
        if candidate.exists() or candidate.is_symlink():
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("managed media namespace escapes storage root")
            if not resolved.is_dir():
                raise ValueError("managed media namespace component is not a directory")
            current = resolved
        else:
            candidate.mkdir()
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("managed media namespace escapes storage root")
            current = resolved
    return current


class LocalStorage:
    def __init__(self, root: Path, root_hdd: Path | None = None):
        self.root = root.resolve()
        self.root_hdd = root_hdd.resolve() if root_hdd else None

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

    async def save_media_source(
        self,
        project_id: UUID,
        media_id: UUID,
        upload: UploadFile,
        max_bytes: int,
    ) -> StoredObject:
        """Stream a non-session source into managed storage with a hard byte limit."""
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        supplied = (upload.filename or "source.bin").replace("\\", "/")
        filename = supplied.rsplit("/", 1)[-1] or "source.bin"
        if filename in {".", ".."}:
            filename = "source.bin"
        relative = PurePosixPath(
            "projects", str(project_id), "media", str(media_id), "source", filename
        )
        resolved_parent = await asyncio.to_thread(
            _ensure_managed_parent, self.root, relative.parent
        )
        target = resolved_parent / filename
        digest = hashlib.sha256()
        size = 0
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise StorageLimitError(f"upload exceeds {max_bytes} byte limit")
                await asyncio.to_thread(_write_chunk, stream, digest, chunk)
            await asyncio.to_thread(stream.flush)
            await asyncio.to_thread(os.fsync, stream.fileno())
        except Exception:
            await asyncio.to_thread(stream.close)
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        else:
            await asyncio.to_thread(stream.close)
        return StoredObject(f"local://{relative.as_posix()}", size, digest.hexdigest())

    def resolve_hdd(self, uri: str) -> Path:
        """Resolve an HDD storage URI to a filesystem path."""
        if not uri.startswith("local-hdd://"):
            raise ValueError(f"Invalid HDD storage URI: {uri}")
        if self.root_hdd is None:
            raise ValueError("HDD storage not configured")
        candidate = self.root_hdd.joinpath(*PurePosixPath(uri[len("local-hdd://") :]).parts).resolve()
        if not candidate.is_relative_to(self.root_hdd):
            raise ValueError("storage URI escapes root")
        return candidate

    async def read_bytes_hdd(self, uri: str) -> bytes:
        """Read bytes from HDD storage."""
        path = self.resolve_hdd(uri)
        return await asyncio.to_thread(path.read_bytes)

    def resolve(self, uri: str) -> Path:
        if not uri.startswith("local://"):
            raise ValueError("unsupported storage URI")
        candidate = self.root.joinpath(*PurePosixPath(uri[8:]).parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("storage URI escapes root")
        return candidate

    def resolve_any(self, uri: str) -> Path:
        """Resolve either local:// or local-hdd:// URI to a filesystem path."""
        if uri.startswith("local-hdd://"):
            return self.resolve_hdd(uri)
        return self.resolve(uri)

    async def delete(self, uri: str) -> None:
        path = self.resolve_any(uri)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def save_source_hdd(
        self, project_id: UUID, session_id: UUID, upload: UploadFile
    ) -> StoredObject:
        """Save source to HDD storage if configured, otherwise falls back to primary storage."""
        if self.root_hdd is None:
            return await self.save_source(project_id, session_id, upload)
        supplied = (upload.filename or "source.bin").replace("\\", "/")
        filename = supplied.rsplit("/", 1)[-1] or "source.bin"
        if filename in {".", ".."}:
            filename = "source.bin"
        relative = PurePosixPath(
            "projects", str(project_id), "sessions", str(session_id), "source", filename
        )
        target = self.root_hdd.joinpath(*relative.parts)
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
        return StoredObject(f"local-hdd://{relative.as_posix()}", size, digest.hexdigest())

    async def save_derived(
        self,
        project_id: UUID,
        session_id: UUID,
        namespace_kind: str,
        namespace_id: UUID,
        collection: str,
        filename: str,
        content: bytes,
    ) -> StoredObject:
        safe_kind = _safe_component(namespace_kind)
        safe_collection = _safe_component(collection)
        safe_filename = _safe_component(filename)
        relative = PurePosixPath(
            "projects",
            str(project_id),
            "sessions",
            str(session_id),
            "derived",
            safe_kind,
            str(namespace_id),
            safe_collection,
            safe_filename,
        )
        target = self.root.joinpath(*relative.parts)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_write_atomic, target, content)
        return StoredObject(
            f"local://{relative.as_posix()}",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )

    async def delete_derived_namespace(
        self,
        project_id: UUID,
        session_id: UUID,
        namespace_kind: str,
        namespace_id: UUID,
    ) -> None:
        safe_kind = _safe_component(namespace_kind)
        relative = PurePosixPath(
            "projects",
            str(project_id),
            "sessions",
            str(session_id),
            "derived",
            safe_kind,
            str(namespace_id),
        )
        target = self.root.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("derived namespace escapes root")
        await asyncio.to_thread(_delete_tree, target)
