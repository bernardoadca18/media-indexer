"""Real Alembic history tests. Only create fresh databases on an explicit test server."""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import MediaAsset, MediaProject
from app.repositories.domain import TemporalDerivativeRepository
from app.services.storage import LocalStorage
from app.services.temporal_derivative import TemporalDerivativeService
from tests.test_temporal_derivatives import request

pytestmark = pytest.mark.postgres
HISTORICAL = "0013_temporal_derivatives"
HEAD = "0014_derivative_media_identity"
GLOBAL = "uq_temporal_derivatives_identity"
COMPOSITE = "uq_temporal_derivatives_media_identity"


@pytest.fixture
async def migration_database():
    admin_url = os.environ.get("MIGRATION_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("requires isolated MIGRATION_TEST_ADMIN_URL")
    database = f"derivative_migration_test_{uuid4().hex}"
    admin = await asyncpg.connect(admin_url.replace("postgresql+asyncpg", "postgresql"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()
    url = admin_url.rsplit("/", 1)[0] + "/" + database
    engine = create_async_engine(url)
    yield url, engine
    # Retain the isolated database as audit evidence; never reset an operational DB.
    await engine.dispose()


async def migrate(url, direction, revision, *, succeeds=True):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", direction, revision,
        env={**os.environ, "DATABASE_URL": url},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    message = output.decode()
    assert (process.returncode == 0) is succeeds, message
    return message


async def snapshot(engine):
    async with engine.connect() as connection:
        constraints = dict((await connection.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'temporal_derivatives'::regclass ORDER BY conname"
        ))).all())
        revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        rows = (await connection.execute(text(
            "SELECT id, source_media_id, identity, status FROM temporal_derivatives ORDER BY id"
        ))).all()
    return revision, constraints, rows


async def test_historical_upgrade_ownership_race_and_protected_downgrade(
    migration_database, tmp_path: Path, monkeypatch,
):
    url, engine = migration_database
    await migrate(url, "upgrade", "0012_provenance_indexes")
    await migrate(url, "upgrade", HISTORICAL)
    revision, constraints, _ = await snapshot(engine)
    assert revision == HISTORICAL
    assert constraints[GLOBAL] == "UNIQUE (identity)"
    assert COMPOSITE not in constraints
    await migrate(url, "upgrade", HEAD)
    revision, constraints, _ = await snapshot(engine)
    assert revision == HEAD
    assert GLOBAL not in constraints
    assert constraints[COMPOSITE] == "UNIQUE (source_media_id, identity)"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        project = MediaProject(name="migration audit")
        db.add(project)
        await db.flush()
        assets = [MediaAsset(
            project_id=project.id, storage_uri=f"local://{uuid4()}.mp4",
            original_filename="same-content.mp4", content_type="video/mp4",
            byte_size=100, sha256="a" * 64, media_kind="video", duration_seconds=3,
        ) for _ in range(2)]
        db.add_all(assets)
        await db.commit()
        ids = [asset.id for asset in assets]

    storage = LocalStorage(tmp_path)
    async def ensure(media_id):
        async with factory() as db:
            return await TemporalDerivativeService(db, storage, 3).ensure(media_id, request("a" * 64))

    # Force both real PostgreSQL transactions to observe absence before insertion.
    original = TemporalDerivativeRepository.by_media_and_identity
    waiting = 0
    barrier = asyncio.Event()
    async def concurrent_lookup(self, media_id, identity):
        nonlocal waiting
        result = await original(self, media_id, identity)
        if result is None and waiting < 2:
            waiting += 1
            if waiting == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(TemporalDerivativeRepository, "by_media_and_identity", concurrent_lookup)
        first, raced = await asyncio.gather(ensure(ids[0]), ensure(ids[0]))
    assert first.derivative.id == raced.derivative.id
    assert first.job.id == raced.job.id
    assert sum([first.created, raced.created]) == 1
    second = await ensure(ids[1])
    assert second.derivative.id != first.derivative.id
    assert second.derivative.source_media_id != first.derivative.source_media_id
    assert second.derivative.identity == first.derivative.identity
    before = await snapshot(engine)
    assert len(before[2]) == 2
    message = await migrate(url, "downgrade", HISTORICAL, succeeds=False)
    assert "duplicate cross-media technical identities" in message
    assert await snapshot(engine) == before
    print("A/C/D/E: historical upgrade, independent ownership, actual insert race, safe downgrade PASS")


async def test_fresh_head_and_compatible_downgrade(migration_database):
    url, engine = migration_database
    await migrate(url, "upgrade", "head")
    revision, constraints, _ = await snapshot(engine)
    assert revision == HEAD
    assert constraints[COMPOSITE] == "UNIQUE (source_media_id, identity)"
    await migrate(url, "downgrade", HISTORICAL)
    revision, constraints, _ = await snapshot(engine)
    assert revision == HISTORICAL
    assert constraints[GLOBAL] == "UNIQUE (identity)"
    await migrate(url, "upgrade", "head")
    print("B: base -> head and compatible downgrade/upgrade PASS")


async def test_reconcile_accidentally_edited_0013(migration_database):
    url, engine = migration_database
    await migrate(url, "upgrade", HISTORICAL)
    async with engine.begin() as connection:
        await connection.execute(text(f"ALTER TABLE temporal_derivatives DROP CONSTRAINT {GLOBAL}"))
        await connection.execute(text(
            f"ALTER TABLE temporal_derivatives ADD CONSTRAINT {COMPOSITE} UNIQUE (source_media_id, identity)"
        ))
    await migrate(url, "upgrade", "head")
    revision, constraints, _ = await snapshot(engine)
    assert revision == HEAD
    assert GLOBAL not in constraints
    assert constraints[COMPOSITE] == "UNIQUE (source_media_id, identity)"
