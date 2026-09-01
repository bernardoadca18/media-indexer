"""Retrieve persisted candidates for one real Note.

Run from backend:
    python scripts/retrieve_note_candidates.py \
        --temporal-index-id UUID --note-id UUID

Requires DATABASE_URL plus valid direct Ollama embedding configuration.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from uuid import UUID

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Note
from app.services.candidate_retrieval import (
    CandidateRetrievalConfig,
    CandidateRetrievalService,
)
from app.services.embedding_provider import ollama_embedding_provider


def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


async def run(temporal_index_id: UUID, note_id: UUID) -> None:
    config = CandidateRetrievalConfig(
        hit_limit=settings.candidate_hit_limit,
        max_candidates=settings.candidate_max_results,
        min_semantic_score=settings.candidate_min_semantic_score,
        max_segment_gap_seconds=settings.candidate_max_segment_gap_seconds,
        context_before_seconds=settings.candidate_context_before_seconds,
        context_after_seconds=settings.candidate_context_after_seconds,
        max_candidate_duration_seconds=settings.candidate_max_duration_seconds,
    )
    try:
        async with AsyncSessionLocal() as db:
            note = await db.get(Note, note_id)
            if note is None:
                raise LookupError("note not found")
            result = await CandidateRetrievalService(db, ollama_embedding_provider).retrieve_notes(
                temporal_index_id,
                [note_id],
                config,
            )
            print("NOTE:")
            print(note.raw)
            print()
            if not result.candidates:
                print("No candidates above the configured semantic threshold.")
                return
            for candidate in result.candidates:
                print(
                    f"{candidate.rank}. "
                    f"{format_timestamp(candidate.start_seconds)}-"
                    f"{format_timestamp(candidate.end_seconds)} "
                    f"score={candidate.score:.4f}"
                )
    finally:
        await ollama_embedding_provider.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temporal-index-id", type=UUID, required=True)
    parser.add_argument("--note-id", type=UUID, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.temporal_index_id, args.note_id))


if __name__ == "__main__":
    main()
