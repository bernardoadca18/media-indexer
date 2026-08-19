"""Test configuration and shared fixtures.

Default fixtures use SQLite for unit tests.
For PostgreSQL integration tests, use @pytest.mark.postgres and request
postgres_session_factory/postgres_db/postgres_client fixtures explicitly.
"""
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_storage
from app.core.database import get_db
from app.main import create_app
from app.models import Base
from app.services.storage import LocalStorage


@pytest.fixture
async def session_factory():
    """SQLite session factory for unit tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    """SQLite database session for unit tests."""
    async with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, tmp_path: Path):
    """TestClient with SQLite backend for unit tests."""
    app = create_app(start_worker=False)

    async def database() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# Import PostgreSQL fixtures so they're available to all tests
pytest_plugins = ["tests.conftest_postgres"]
