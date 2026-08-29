"""Test configuration and shared fixtures.

Default fixtures use SQLite for unit tests.
For PostgreSQL integration tests, use @pytest.mark.postgres and request
postgres_session_factory/postgres_db/postgres_client fixtures explicitly.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db as get_api_db
from app.api.dependencies import get_storage
from app.core.database import get_db as get_core_db
from app.main import create_app
from app.models import Base
from app.services.storage import LocalStorage


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--postgres",
        action="store_true",
        default=False,
        help="run explicit PostgreSQL/pgvector integration tests",
    )
    parser.addoption(
        "--external",
        action="store_true",
        default=False,
        help="run explicit external-provider/model smoke tests",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep the default suite local/CPU-only and require explicit integration opt-ins."""

    run_postgres = config.getoption("--postgres")
    run_external = config.getoption("--external")
    postgres_skip = pytest.mark.skip(reason="requires explicit --postgres")
    external_skip = pytest.mark.skip(reason="requires explicit --external")
    for item in items:
        if "postgres" in item.keywords and not run_postgres:
            item.add_marker(postgres_skip)
        if "external" in item.keywords and not run_external:
            item.add_marker(external_skip)


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
async def client(session_factory, tmp_path: Path):
    """Event-loop-local ASGI client with SQLite backend for API unit tests."""
    app = create_app(start_worker=False)

    async def database() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    # Most routers use the API dependency wrapper while media search imports the
    # core dependency directly. Override both so unit tests never reach the real
    # PostgreSQL configured in the developer environment.
    app.dependency_overrides[get_api_db] = database
    app.dependency_overrides[get_core_db] = database
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        test_client.app = app  # type: ignore[attr-defined]
        yield test_client


# Import PostgreSQL fixtures so they're available to all tests
pytest_plugins = ["tests.conftest_postgres"]
