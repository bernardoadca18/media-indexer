"""Run real multimodal alignment for persisted retrieval candidates.

Run from backend:
    python scripts/smoke_multimodal_alignment.py \
        --retrieval-run-id UUID [--candidate-id UUID ...]

Requires PostgreSQL, FFmpeg, source media under STORAGE_ROOT, and a valid
LiteLLM credential/model alias. No media or image bytes are persisted in JSON.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from uuid import UUID

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.repositories.alignment import AlignmentRepository
from app.services.alignment import (
    AlignmentConfig,
    AlignmentProcessor,
    AlignmentRequest,
    AlignmentService,
)
from app.services.alignment_provider import LiteLLMMultimodalAlignmentProvider
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.storage import LocalStorage
from app.services.visual_tools import FFmpegFrameExtractor


def configured_alignment() -> AlignmentConfig:
    return AlignmentConfig(
        frame_interval_seconds=settings.alignment_frame_interval_seconds,
        max_frames=settings.alignment_max_frames,
        frame_width=settings.alignment_frame_width,
        jpeg_quality=settings.alignment_jpeg_quality,
        frame_dedup_tolerance_seconds=(settings.alignment_frame_dedup_tolerance_seconds),
        max_transcript_characters=settings.alignment_max_transcript_characters,
        refinement_enabled=settings.alignment_refinement_enabled,
        refinement_frame_interval_seconds=(settings.alignment_refinement_frame_interval_seconds),
        refinement_padding_seconds=settings.alignment_refinement_padding_seconds,
        refinement_max_frames=settings.alignment_refinement_max_frames,
        early_stop_confidence=settings.alignment_early_stop_confidence,
    )


async def execute(
    retrieval_run_id: UUID,
    candidate_ids: tuple[UUID, ...] | None,
) -> UUID:
    provider = LiteLLMMultimodalAlignmentProvider(
        settings.litellm_base_url,
        settings.litellm_api_key,
        settings.alignment_timeout_seconds,
    )
    storage = LocalStorage(settings.storage_root)
    extractor = FFmpegFrameExtractor(settings.ffmpeg_binary)
    async with AsyncSessionLocal() as db:
        ensured = await AlignmentService(
            db,
            provider_name=provider.name,
            max_attempts=settings.job_max_attempts,
        ).ensure(
            retrieval_run_id,
            AlignmentRequest(
                model=settings.alignment_model,
                candidate_ids=candidate_ids,
                config=configured_alignment(),
            ),
        )
        if ensured.job.status != "completed":
            registry = ProcessorRegistry(
                {
                    "multimodal_alignment": lambda active_db: AlignmentProcessor(
                        active_db,
                        storage,
                        extractor,
                        provider,
                    )
                }
            )
            if not await JobExecutor(db, registry).execute(ensured.job.id):
                await db.refresh(ensured.job)
                raise RuntimeError(ensured.job.error or "alignment job failed")
        repository = AlignmentRepository(db)
        run = await repository.get_run(ensured.alignment_run.id)
        if run is None:
            raise RuntimeError("alignment run disappeared")
        results, total = await repository.results(run.id, 0, 1000)
        assets = await repository.alignment_assets(run.id)
        print(
            {
                "alignment_run_id": str(run.id),
                "status": run.status,
                "candidate_results": total,
                "managed_frames": len(assets),
                "model": run.model,
            }
        )
        for result in results:
            print(
                {
                    "retrieval_rank": result.retrieval_rank,
                    "status": result.status,
                    "start_seconds": result.start_seconds,
                    "end_seconds": result.end_seconds,
                    "confidence": result.confidence,
                    "timestamp_resolution_seconds": (result.timestamp_resolution_seconds),
                }
            )
        return run.id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-run-id", type=UUID, required=True)
    parser.add_argument("--candidate-id", type=UUID, action="append")
    args = parser.parse_args()
    candidate_ids = tuple(args.candidate_id) if args.candidate_id else None
    asyncio.run(execute(args.retrieval_run_id, candidate_ids))


if __name__ == "__main__":
    main()
