"""PostgreSQL integration test configuration.

Tests that require pgvector-specific features (like cosine_distance) should:
1. Be marked with @pytest.mark.postgres
2. Use postgres_session_factory, postgres_db, or postgres_client fixtures

The postgres fixtures are separate from the default SQLite fixtures to avoid
interference with unit tests.

The database should be created and migrated via alembic before running tests:
    docker exec media-indexer-db-1 psql -U media_indexer -d postgres -c "CREATE DATABASE media_indexer_test;"
    DATABASE_URL="postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer_test" alembic upgrade head
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_db as get_api_db
from app.api.dependencies import get_storage
from app.core.database import get_db as get_core_db
from app.main import create_app
from app.services.storage import LocalStorage

POSTGRES_TEST_URL = os.getenv(
    "POSTGRES_TEST_URL",
    "postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer_test",
)


@pytest.fixture
async def postgres_engine():
    """Create PostgreSQL engine for integration tests.

    Assumes the database already exists and has been migrated via alembic.
    Tables are truncated between tests, not dropped.
    """
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def postgres_session_factory(postgres_engine):
    """Create PostgreSQL session factory for integration tests."""
    return async_sessionmaker(postgres_engine, expire_on_commit=False)


@pytest.fixture
async def postgres_db(postgres_session_factory) -> AsyncIterator[AsyncSession]:
    """Provide PostgreSQL session for integration tests.

    Truncates all tables before each test for isolation.
    """
    async with postgres_session_factory() as session:
        # Truncate all tables before each test for isolation
        result = await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        tables = [row[0] for row in result.fetchall()]
        if tables:
            await session.execute(text(f"TRUNCATE TABLE {', '.join(tables)} CASCADE"))
            await session.commit()
        yield session


@pytest.fixture
async def postgres_client(postgres_session_factory, tmp_path: Path):
    """Provide an event-loop-local ASGI client with PostgreSQL backend."""
    app = create_app(start_worker=False)

    async def database() -> AsyncIterator[AsyncSession]:
        async with postgres_session_factory() as session:
            yield session

    app.dependency_overrides[get_api_db] = database
    app.dependency_overrides[get_core_db] = database
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        test_client.app = app  # type: ignore[attr-defined]
        yield test_client
