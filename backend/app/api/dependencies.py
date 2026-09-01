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
from app.services.storage import LocalStorage, Storage


@lru_cache
def get_storage() -> Storage:
    return LocalStorage(settings.storage_root)


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return ollama_embedding_provider


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in _get_db():
        yield session


EmbeddingDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]

DB = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[Storage, Depends(get_storage)]
