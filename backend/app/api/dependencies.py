from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.services.embedding_provider import (
    EmbeddingProvider,
    LiteLLMEmbeddingProvider,
)
from app.services.storage import LocalStorage, Storage


@lru_cache
def get_storage() -> Storage:
    return LocalStorage(settings.storage_root)


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return LiteLLMEmbeddingProvider(
        settings.litellm_base_url,
        settings.litellm_api_key,
        settings.embedding_timeout_seconds,
    )


EmbeddingDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]

DB = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[Storage, Depends(get_storage)]
