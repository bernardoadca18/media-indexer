#!/usr/bin/env python3
"""Preflight and resumable API adapter for the operator-managed GTA corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.services.gta_corpus import (
    CorpusPreflightReport,
    ImageCorpusEntry,
    VideoCorpusEntry,
    preflight_gta_corpus,
    render_preflight_report,
)

TIMELINE_PROVENANCE = "operator_semantic_timeline_import"
OPERATOR_BATCH_VERSION = "gta_canonical_corpus_operator_v1"


@dataclass(slots=True)
class BatchCounts:
    completed: int = 0
    pending: int = 0
    failed: int = 0
    reused: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only GTA corpus preflight and resumable canonical API ingestion"
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8012")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=("all", "temporal", "static"), default="all")
    parser.add_argument("--smoke", choices=("initial", "trailer1"))
    parser.add_argument("--labels", help="Comma-separated canonical video labels to ingest")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--wait-timeout-seconds", type=float, default=7200.0)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--ffprobe-binary", default="ffprobe")
    return parser.parse_args()


async def wait_for_job(client: httpx.AsyncClient, job_id: str, timeout_seconds: float) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/jobs/{job_id}")
        response.raise_for_status()
        status = str(response.json()["status"])
        if status in {"completed", "failed"}:
            return status
        await asyncio.sleep(1.0)
    return "pending"


async def _settle_job(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    *,
    wait: bool,
    timeout_seconds: float,
) -> str:
    job = payload["job"]
    status = str(job["status"])
    if wait and status not in {"completed", "failed"}:
        return await wait_for_job(client, str(job["id"]), timeout_seconds)
    return status


def _record(counts: BatchCounts, status: str, *, reused: bool = False) -> None:
    if reused:
        counts.reused += 1
    if status == "completed":
        counts.completed += 1
    elif status == "failed":
        counts.failed += 1
    else:
        counts.pending += 1


async def ingest_temporal_entry(
    client: httpx.AsyncClient,
    entry: VideoCorpusEntry,
    project_id: str,
    *,
    wait: bool,
    timeout_seconds: float,
) -> tuple[str, bool]:
    idempotency_key = f"{OPERATOR_BATCH_VERSION}:{entry.label}:{entry.sha256}"
    content_type = "video/x-matroska" if entry.path.suffix.casefold() == ".mkv" else "video/mp4"
    with entry.path.open("rb") as video:
        response = await client.post(
            f"/projects/{project_id}/sessions",
            headers={"Idempotency-Key": idempotency_key},
            data={"label": entry.label},
            files={"video": (entry.path.name, video, content_type)},
        )
    response.raise_for_status()
    ingested = response.json()
    initial_status = await _settle_job(client, ingested, wait=wait, timeout_seconds=timeout_seconds)
    session_id = str(ingested["session"]["id"])
    if initial_status != "completed":
        print(
            f"TEMPORAL label={entry.label} session_id={session_id} "
            f"initial_status={initial_status}"
        )
        return initial_status, False
    session_response = await client.get(f"/sessions/{session_id}")
    session_response.raise_for_status()
    if session_response.json()["status"] != "ready":
        return "failed", False

    notes_response = await client.post(
        f"/sessions/{session_id}/timeline-notes/import",
        json={
            "version": "timeline_notes_v1",
            "provenance": TIMELINE_PROVENANCE,
            "observations": entry.observations,
        },
    )
    notes_response.raise_for_status()
    note_result = notes_response.json()
    if note_result.get("errors"):
        raise RuntimeError(f"timeline import failed: {note_result['errors']}")

    transcription_response, visual_response = await asyncio.gather(
        client.post(f"/sessions/{session_id}/transcriptions", json={}),
        client.post(f"/sessions/{session_id}/visual-indexes", json={}),
    )
    transcription_response.raise_for_status()
    visual_response.raise_for_status()
    transcription = transcription_response.json()
    visual = visual_response.json()
    transcription_status, visual_status = await asyncio.gather(
        _settle_job(client, transcription, wait=wait, timeout_seconds=timeout_seconds),
        _settle_job(client, visual, wait=wait, timeout_seconds=timeout_seconds),
    )
    if "failed" in {transcription_status, visual_status}:
        print(
            f"TEMPORAL label={entry.label} session_id={session_id} "
            f"transcription_status={transcription_status} visual_status={visual_status}"
        )
        return "failed", not (transcription["created"] or visual["created"])
    if transcription_status != "completed" or visual_status != "completed":
        print(
            f"TEMPORAL label={entry.label} session_id={session_id} "
            f"transcription_status={transcription_status} visual_status={visual_status}"
        )
        return "pending", not (transcription["created"] or visual["created"])

    temporal_response = await client.post(
        f"/sessions/{session_id}/temporal-indexes",
        json={
            "transcription_id": transcription["transcription"]["id"],
            "visual_index_id": visual["visual_index"]["id"],
        },
    )
    temporal_response.raise_for_status()
    temporal = temporal_response.json()
    temporal_status = await _settle_job(
        client, temporal, wait=wait, timeout_seconds=timeout_seconds
    )
    reused = (
        note_result["imported_count"] == 0
        and not transcription["created"]
        and not visual["created"]
        and not temporal["created"]
    )
    print(
        f"TEMPORAL label={entry.label} session_id={session_id} "
        f"media_id={ingested['session']['media_id']} "
        f"transcription_id={transcription['transcription']['id']} "
        f"visual_index_id={visual['visual_index']['id']} "
        f"temporal_index_id={temporal['temporal_index']['id']} status={temporal_status}"
        f" sha256={entry.sha256}"
    )
    return temporal_status, reused


async def ingest_static_entry(
    client: httpx.AsyncClient,
    entry: ImageCorpusEntry,
    project_id: str,
    *,
    wait: bool,
    timeout_seconds: float,
) -> tuple[str, bool]:
    assert entry.content_type is not None
    with entry.path.open("rb") as image:
        response = await client.post(
            f"/projects/{project_id}/images",
            data={
                "provenance": json.dumps(entry.provenance, ensure_ascii=False),
                "enqueue_semantic_index": "false",
            },
            files={"image": (entry.path.name, image, entry.content_type)},
        )
    response.raise_for_status()
    ingested = response.json()
    semantic_response = await client.post(
        f"/media-assets/{ingested['media_asset']['id']}/operator-image-semantics",
        json={
            "version": "operator_semantic_import_v1",
            "semantic_source": "human_assisted_import",
            "parser_version": "operator_semantic_markdown_v1",
            "semantic_text": entry.semantic_text,
            "sidecar_sha256": entry.sidecar_sha256,
        },
    )
    semantic_response.raise_for_status()
    semantic = semantic_response.json()
    status = await _settle_job(client, semantic, wait=wait, timeout_seconds=timeout_seconds)
    print(
        f"STATIC path={entry.path.name} media_id={ingested['media_asset']['id']} "
        f"image_semantic_index_id={semantic['semantic_index']['id']} status={status}"
    )
    return status, not ingested["media_created"] and not semantic["created"]


async def execute_batch(
    client: httpx.AsyncClient,
    report: CorpusPreflightReport,
    *,
    project_id: str,
    wait: bool,
    timeout_seconds: float,
    concurrency: int,
) -> BatchCounts:
    project = await client.get(f"/projects/{project_id}")
    project.raise_for_status()
    semaphore = asyncio.Semaphore(concurrency)
    counts = BatchCounts()

    async def temporal(entry: VideoCorpusEntry) -> tuple[str, bool]:
        async with semaphore:
            return await ingest_temporal_entry(
                client,
                entry,
                project_id,
                wait=wait,
                timeout_seconds=timeout_seconds,
            )

    async def static(entry: ImageCorpusEntry) -> tuple[str, bool]:
        async with semaphore:
            return await ingest_static_entry(
                client,
                entry,
                project_id,
                wait=wait,
                timeout_seconds=timeout_seconds,
            )

    tasks = [temporal(entry) for entry in report.videos]
    tasks.extend(static(entry) for entry in report.images)
    for task in asyncio.as_completed(tasks):
        try:
            status, reused = await task
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc!r}")
            _record(counts, "failed")
        else:
            _record(counts, status, reused=reused)
    return counts


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if args.wait_timeout_seconds <= 0:
        raise ValueError("wait timeout must be positive")
    labels_arg = getattr(args, "labels", None)
    labels = (
        frozenset(label.strip() for label in labels_arg.split(",") if label.strip())
        if labels_arg
        else None
    )
    report = await preflight_gta_corpus(
        args.corpus_root,
        only=args.only,
        smoke=args.smoke,
        labels=labels,
        concurrency=args.concurrency,
        ffprobe_binary=args.ffprobe_binary,
    )
    print(render_preflight_report(report))
    if args.json_report is not None:
        report_path = args.json_report.resolve()
        if report_path.is_relative_to(report.corpus_root):
            raise ValueError("JSON report must not be written inside the source corpus")
        args.json_report.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if not report.ok:
        print("Batch blocked: resolve preflight errors before ingestion.")
        return 2
    if args.dry_run:
        print("Dry-run complete: no API request was made.")
        return 0

    timeout = httpx.Timeout(connect=10.0, read=7200.0, write=7200.0, pool=120.0)
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=timeout) as client:
        counts = await execute_batch(
            client,
            report,
            project_id=args.project_id,
            wait=args.wait,
            timeout_seconds=args.wait_timeout_seconds,
            concurrency=args.concurrency,
        )
    print(
        f"Batch completed={counts.completed} pending={counts.pending} "
        f"failed={counts.failed} reused={counts.reused}"
    )
    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
