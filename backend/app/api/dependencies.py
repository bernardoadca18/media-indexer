from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db as _get_db
from app.services.embedding_provider import (
    EmbeddingProvider,
    ollama_embedding_provider,
)
from app.services.image_semantic_provider import (
    ImageSemanticProvider,
    NinerouterImageSemanticProvider,
)
from app.services.storage import LocalStorage, Storage


@lru_cache
def get_storage() -> Storage:
    # Validate HDD configuration consistency
    # Both STORAGE_ROOT_HDD and the Docker mount must be configured together
    hdd_backend_configured = settings.storage_root_hdd is not None
    hdd_mount_configured = settings.media_indexer_hdd_mount_path is not None

    if hdd_backend_configured and not hdd_mount_configured:
        raise RuntimeError(
            "HDD configuration error: STORAGE_ROOT_HDD is set but the HDD mount is not "
            "configured. Set MEDIA_INDEXER_HDD_HOST_PATH in your environment to enable HDD "
            "storage, or remove STORAGE_ROOT_HDD to disable it."
        )

    if hdd_mount_configured and not hdd_backend_configured:
        raise RuntimeError(
            "HDD configuration error: HDD mount is configured but STORAGE_ROOT_HDD is not set. "
            "Set STORAGE_ROOT_HDD=/data/media-hdd in your environment to use HDD storage, "
            "or remove MEDIA_INDEXER_HDD_HOST_PATH to disable it."
        )

    return LocalStorage(
        settings.storage_root,
        root_hdd=settings.storage_root_hdd,
    )


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return ollama_embedding_provider


@lru_cache
def get_image_semantic_provider() -> ImageSemanticProvider:
    return NinerouterImageSemanticProvider(settings.image_vision_timeout_seconds)


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in _get_db():
        yield session


EmbeddingDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
ImageSemanticProviderDep = Annotated[
    ImageSemanticProvider, Depends(get_image_semantic_provider)
]

DB = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[Storage, Depends(get_storage)]
