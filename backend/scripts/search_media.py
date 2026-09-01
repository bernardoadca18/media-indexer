#!/usr/bin/env python3
"""Manual evaluation script for Media Search.

This script allows testing Media Search with real data through direct Ollama embeddings.

Usage:
    python scripts/search_media.py --project-id <UUID> --query "Geralt learning Aard"

For session-specific search:
    python scripts/search_media.py --session-id <UUID> --query "combat against drowners"

Options:
    --project-id UUID   Search across all sessions in the project
    --session-id UUID   Search within a specific session
    --query TEXT        Search query (required)
    --max-results N     Maximum results to return (default: 10)
    --min-score F       Minimum relevance score (default: 0.25)
    --format FORMAT     Output format: text (default), json

Environment:
    DATABASE_URL: PostgreSQL async connection string
    OLLAMA_BASE_URL: Ollama service base URL (without /api)
    EMBEDDING_MODEL: embedding-capable Ollama model tag
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.schemas.media_search import MediaSearchRequest
from app.services.embedding_provider import ollama_embedding_provider
from app.services.media_search import MediaSearchService


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def print_text_results(response: Any) -> None:
    """Print results in human-readable text format."""
    print(f'\nQuery: "{response.query}"')
    print(f"Scope: {response.scope}")
    print(f"Strategy: {response.strategy}")

    if response.scope == "project":
        print(f"Project ID: {response.project_id}")
        print(f"Sessions searched: {response.searched_sessions}")
        print(f"Sessions skipped: {response.skipped_sessions}")
    else:
        print(f"Session ID: {response.session_id}")

    print(
        f"\nEmbedding: {response.embedding_provider}/{response.embedding_model} ({response.embedding_dimensions}d)"
    )

    print(f"\n{'=' * 60}")
    print(f"RESULTS ({len(response.results)} total)")
    print(f"{'=' * 60}")

    if not response.results:
        print("No results found.")
        return

    for i, result in enumerate(response.results, 1):
        print(f"\n{i}. {result.session_label}")
        print(
            f"   Range: {format_timestamp(result.start_seconds)} → {format_timestamp(result.end_seconds)}"
        )
        print(f"   Duration: {result.duration_seconds:.1f}s")
        print(f"   Score: {result.score:.3f} (rank {result.rank})")

        if result.transcript_preview:
            preview = result.transcript_preview[:100]
            if len(result.transcript_preview) > 100:
                preview += "..."
            print(f'   Preview: "{preview}"')

        if result.matched_note_ids:
            print(f"   Matched notes: {len(result.matched_note_ids)}")

    if response.scope == "project" and response.session_summaries:
        print(f"\n{'=' * 60}")
        print("SESSION SUMMARY")
        print(f"{'=' * 60}")

        for summary in response.session_summaries:
            status = "✓ searched" if summary.searched else f"✗ skipped: {summary.skip_reason}"
            print(f"  {summary.label}: {status} ({summary.result_count} results)")


def print_json_results(response: Any) -> None:
    """Print results as JSON."""
    data = response.model_dump()
    print(json.dumps(data, indent=2, default=str))


async def search_session(
    db: AsyncSession,
    session_id: UUID,
    query: str,
    max_results: int,
    min_score: float,
    output_format: str,
) -> int:
    """Execute session search and print results."""
    service = MediaSearchService(db, ollama_embedding_provider)

    request = MediaSearchRequest(
        query=query,
        max_results=max_results,
        min_score=min_score,
    )

    try:
        response = await service.search_session(session_id, request)

        if output_format == "json":
            print_json_results(response)
        else:
            print_text_results(response)

        return 0

    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 404
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


async def search_project(
    db: AsyncSession,
    project_id: UUID,
    query: str,
    max_results: int,
    min_score: float,
    output_format: str,
) -> int:
    """Execute project search and print results."""
    service = MediaSearchService(db, ollama_embedding_provider)

    request = MediaSearchRequest(
        query=query,
        max_results=max_results,
        min_score=min_score,
    )

    try:
        response = await service.search_project(project_id, request)

        if output_format == "json":
            print_json_results(response)
        else:
            print_text_results(response)

        return 0

    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 404
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="Media Search evaluation tool")
    parser.add_argument("--project-id", type=UUID, help="Project UUID to search")
    parser.add_argument("--session-id", type=UUID, help="Session UUID to search")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum results")
    parser.add_argument("--min-score", type=float, default=0.25, help="Minimum score")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    args = parser.parse_args()

    if not args.project_id and not args.session_id:
        print("Error: Must specify --project-id or --session-id", file=sys.stderr)
        return 1

    if args.project_id and args.session_id:
        print("Error: Cannot specify both --project-id and --session-id", file=sys.stderr)
        return 1

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with async_session() as db:
            if args.session_id:
                return await search_session(
                    db,
                    args.session_id,
                    args.query,
                    args.max_results,
                    args.min_score,
                    args.format,
                )
            return await search_project(
                db,
                args.project_id,
                args.query,
                args.max_results,
                args.min_score,
                args.format,
            )
    finally:
        await ollama_embedding_provider.close()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
