"""Smoke-test the configured 9Router embedding alias.

Run from backend:
    python scripts/smoke_embedding.py --text "Geralt learns Aard"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio

from app.core.config import settings
from app.services.embedding_provider import NinerouterEmbeddingProvider


async def run(text: str) -> None:
    provider = NinerouterEmbeddingProvider(settings.embedding_timeout_seconds)
    vectors = await provider.embed(
        [text],
        model=settings.embedding_model,
        expected_dimensions=settings.embedding_dimensions,
    )
    print(
        {
            "provider": provider.name,
            "model": settings.embedding_model,
            "dimensions": len(vectors[0]),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.text))


if __name__ == "__main__":
    main()
