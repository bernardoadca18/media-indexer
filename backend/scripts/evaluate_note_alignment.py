"""Evaluate note alignments for a real persisted RetrievalRun.

Run from backend:
    python scripts/evaluate_note_alignment.py --retrieval-run-id UUID

This executes/reuses the configured alignment run, then prints every raw Note,
candidate decision, selected interval, confidence and timestamp resolution.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from uuid import UUID

from smoke_multimodal_alignment import execute
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import CandidateAlignment, Note
from app.repositories.alignment import AlignmentRepository


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total_milliseconds = max(0, round(seconds * 1000))
    total_seconds, milliseconds = divmod(total_milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


async def run(retrieval_run_id: UUID) -> None:
    alignment_run_id = await execute(retrieval_run_id, None)
    async with AsyncSessionLocal() as db:
        repository = AlignmentRepository(db)
        subjects = await repository.subjects(alignment_run_id)
        for subject in subjects:
            print()
            if subject.note_id is not None:
                note = await db.get(Note, subject.note_id)
                print(f"NOTE {note.order if note else '?'}:")
                print(note.raw if note else subject.observation)
            else:
                print("QUERY:")
                print(subject.observation)
            print(f"OUTCOME: {subject.status}")
            rows = (
                await db.execute(
                    select(CandidateAlignment)
                    .where(CandidateAlignment.alignment_subject_id == subject.id)
                    .order_by(CandidateAlignment.retrieval_rank)
                )
            ).scalars()
            for result in rows:
                selected = "*" if subject.selected_candidate_alignment_id == result.id else " "
                print(
                    f"{selected} rank={result.retrieval_rank} "
                    f"status={result.status} "
                    f"interval={format_timestamp(result.start_seconds)}-"
                    f"{format_timestamp(result.end_seconds)} "
                    f"confidence={result.confidence} "
                    f"resolution={result.timestamp_resolution_seconds}"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-run-id", type=UUID, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.retrieval_run_id))


if __name__ == "__main__":
    main()
