#!/usr/bin/env python3
"""Idempotently ingest a bounded batch of static images through the public API."""

import argparse
import asyncio
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Counts:
    found: int = 0
    created: int = 0
    reused: int = 0
    ready: int = 0
    pending: int = 0
    failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8012")
    parser.add_argument("--source-owner")
    parser.add_argument("--source-class")
    parser.add_argument("--asset-class")
    parser.add_argument("--collection")
    parser.add_argument("--game")
    parser.add_argument("--declared-metadata", default="{}")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("IMAGE_BATCH_CONCURRENCY", "1")),
    )
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=float, default=900.0)
    return parser.parse_args()


def scan(directory: Path) -> list[Path]:
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("directory must reference a directory")
    files = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            continue
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(root):
            files.append(resolved)
    return sorted(files)


async def wait_for_job(
    client: httpx.AsyncClient, job_id: str, timeout_seconds: float
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/jobs/{job_id}")
        response.raise_for_status()
        status = str(response.json()["status"])
        if status in {"completed", "failed"}:
            return status
        await asyncio.sleep(1.0)
    return "pending"


async def ingest_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    path: Path,
    project_id: str,
    provenance: dict[str, Any],
    wait: bool,
    wait_timeout_seconds: float,
) -> tuple[str, str]:
    async with semaphore:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as stream:
                response = await client.post(
                    f"/projects/{project_id}/images",
                    data={
                        "provenance": json.dumps(provenance, ensure_ascii=False),
                        "enqueue_semantic_index": "true",
                    },
                    files={"image": (path.name, stream, content_type)},
                )
            response.raise_for_status()
            payload = response.json()
            disposition = "created" if payload["media_created"] else "reused"
            status = payload["semantic_index"]["status"]
            if wait and status not in {"completed", "failed"}:
                status = await wait_for_job(
                    client, payload["job"]["id"], wait_timeout_seconds
                )
            return disposition, status
        except Exception as exc:
            print(f"FAILED {path.name}: {exc}")
            return "failed", "failed"


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    metadata = json.loads(args.declared_metadata)
    if not isinstance(metadata, dict):
        raise ValueError("--declared-metadata must be a JSON object")
    provenance = {
        key: value
        for key, value in {
            "source_owner": args.source_owner,
            "source_class": args.source_class,
            "asset_class": args.asset_class,
            "collection": args.collection,
            "game": args.game,
            "declared_metadata": metadata,
        }.items()
        if value is not None
    }
    paths = scan(args.directory)
    counts = Counts(found=len(paths))
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=120.0) as client:
        tasks = [
            ingest_one(
                client,
                semaphore,
                path,
                args.project_id,
                provenance,
                args.wait,
                args.wait_timeout_seconds,
            )
            for path in paths
        ]
        for task in asyncio.as_completed(tasks):
            disposition, status = await task
            if disposition == "created":
                counts.created += 1
            elif disposition == "reused":
                counts.reused += 1
            if status == "completed":
                counts.ready += 1
            elif status == "failed":
                counts.failed += 1
            else:
                counts.pending += 1
    print(
        f"Found: {counts.found} Created: {counts.created} Reused: {counts.reused} "
        f"Semantic ready: {counts.ready} Pending: {counts.pending} Failed: {counts.failed}"
    )
    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
