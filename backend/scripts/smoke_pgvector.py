"""Validate pgvector availability and exact cosine ordering.

Run from backend after migrations:
    python scripts/smoke_pgvector.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


async def run() -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            if version is None:
                raise RuntimeError("pgvector extension is not enabled")
            await connection.execute(
                text("CREATE TEMP TABLE vector_smoke (label text, embedding vector(3))")
            )
            await connection.execute(
                text(
                    "INSERT INTO vector_smoke (label, embedding) VALUES "
                    "('near', '[1,0,0]'), ('far', '[0,1,0]')"
                )
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT label, 1 - (embedding <=> '[0.9,0.1,0]'::vector) AS score "
                        "FROM vector_smoke ORDER BY embedding <=> '[0.9,0.1,0]'::vector"
                    )
                )
            ).all()
            if [row.label for row in rows] != ["near", "far"]:
                raise RuntimeError("unexpected cosine similarity ordering")
            print(
                {
                    "pgvector_version": version,
                    "metric": "cosine",
                    "ordered_labels": [row.label for row in rows],
                    "scores": [float(row.score) for row in rows],
                }
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
