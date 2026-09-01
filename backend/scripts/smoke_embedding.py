"""Smoke-test the configured direct Ollama embedding model.

Run from backend:
    python scripts/smoke_embedding.py --text "Geralt learns Aard"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio

from app.core.config import settings
from app.services.embedding_provider import ollama_embedding_provider


async def run(text: str) -> None:
    try:
        vectors = await ollama_embedding_provider.embed(
            [text],
            model=settings.embedding_model,
            expected_dimensions=settings.embedding_dimensions,
        )
        print(
            {
                "provider": ollama_embedding_provider.name,
                "model": settings.embedding_model,
                "dimensions": len(vectors[0]),
            }
        )
    finally:
        await ollama_embedding_provider.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.text))


if __name__ == "__main__":
    main()
