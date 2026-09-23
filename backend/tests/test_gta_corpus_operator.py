import argparse
import io
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.services.gta_corpus import (
    CorpusPreflightReport,
    ImageCorpusEntry,
    VideoCorpusEntry,
    _expected_videos,
    preflight_gta_corpus,
)
from app.services.probe import ProbeResult
from scripts import gta_corpus_operator


class FakeProbe:
    def __init__(self, binary: str = "ffprobe") -> None:
        self.binary = binary

    async def inspect(self, path: Path) -> ProbeResult:
        return ProbeResult(12.0, "matroska", "h264", "aac", 30.0, 1920, 1080, {})


def image_bytes(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="JPEG")
    return output.getvalue()


def write_image_pair(path: Path, color: str = "red") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes(color))
    path.with_suffix(".md").write_text(
        "Operator-authored semantic description.\n", encoding="utf-8"
    )


def corpus_tree(root: Path) -> None:
    for game in ("gta_iv", "gta_v", "gta_vi"):
        (root / game).mkdir(parents=True)
    video = root / "gta_iv" / "000.mkv"
    video.write_bytes(b"synthetic-video")
    video.with_suffix(".md").write_text("[00:00–00:12] Synthetic observation.\n")
    image_root = root / "gta_vi" / "Images"
    write_image_pair(
        image_root / "GTAVI_Screenshots" / "People" / "Lucia Caminos" / "lucia.jpg",
        "red",
    )
    artwork = image_root / "GTAVI_Artwork_Wallpapers" / "Lucia" / "lucia_landscape.jpg"
    write_image_pair(artwork, "blue")
    (artwork.parent / "lucia_phone.jpg").write_bytes(image_bytes("green"))
    write_image_pair(image_root / "GTAVI_Vintage_Vice_City_Pack" / "vintage.jpg", "yellow")


def test_temporal_manifest_has_22_unique_canonical_labels(tmp_path: Path) -> None:
    labels = [item.label for item in _expected_videos(tmp_path)]
    assert len(labels) == len(set(labels)) == 22
    assert labels[:5] == [
        "gta_iv_gameplay_ep000",
        "gta_iv_gameplay_ep001",
        "gta_iv_gameplay_ep002",
        "gta_iv_gameplay_ep003",
        "gta_iv_gameplay_ep004",
    ]
    entries = {item.label: item for item in _expected_videos(tmp_path)}
    assert entries["gta_iv_gameplay_ep003"].path.suffix == ".mp4"
    assert entries["gta_iv_gameplay_ep004"].path.suffix == ".mkv"
    assert "gta_v_gameplay_ep005" in labels
    assert "gta_vi_official_trailer_1" in labels
    assert "gta_vi_official_extended_look" in labels
    assert "gta_vi_official_cover_art_animation" in labels
    assert "gta_vi_official_clip_boobie_ike" in labels


async def test_smoke_preflight_pairs_images_filters_artwork_and_derives_provenance(
    monkeypatch, tmp_path: Path
) -> None:
    corpus_tree(tmp_path)
    monkeypatch.setattr("app.services.gta_corpus.FFprobe", FakeProbe)
    report = await preflight_gta_corpus(tmp_path, smoke="initial")
    assert report.ok
    assert len(report.videos) == 1
    assert len(report.images) == 3
    assert report.excluded_noncanonical_artworks == 1
    screenshot = next(item for item in report.images if item.kind == "screenshot")
    artwork = next(item for item in report.images if item.kind == "artwork")
    vintage = next(item for item in report.images if item.kind == "vintage")
    assert screenshot.provenance["declared_metadata"] == {"known_subject": "Lucia Caminos"}
    assert artwork.provenance["asset_class"] == "official_artwork"
    assert artwork.provenance["collection"] == "artwork_wallpapers"
    assert vintage.provenance["asset_class"] == "official_promotional"
    assert report.videos[0].label == "gta_iv_gameplay_ep000"


async def test_preflight_reports_missing_pair_and_expected_count(
    monkeypatch, tmp_path: Path
) -> None:
    corpus_tree(tmp_path)
    monkeypatch.setattr("app.services.gta_corpus.FFprobe", FakeProbe)
    missing = next((tmp_path / "gta_vi" / "Images" / "GTAVI_Screenshots").rglob("*.md"))
    missing.unlink()
    report = await preflight_gta_corpus(tmp_path, only="static")
    codes = {item.code for item in report.issues}
    assert "missing_sidecar" in codes
    assert "static_count_mismatch" in codes
    assert not report.ok


async def test_selective_preflight_excludes_only_declared_004_derivatives(
    monkeypatch, tmp_path: Path
) -> None:
    for game in ("gta_iv", "gta_v", "gta_vi"):
        (tmp_path / game).mkdir()
    for entry in _expected_videos(tmp_path):
        entry.path.write_bytes(b"video")
        entry.sidecar_path.write_text("[00:00–00:12] Synthetic observation.\n")
    for stem in ("004", "004_A", "004_B", "004_C"):
        (tmp_path / "gta_iv" / f"{stem}.mp4").write_bytes(b"derivative")
    monkeypatch.setattr("app.services.gta_corpus.FFprobe", FakeProbe)
    report = await preflight_gta_corpus(
        tmp_path,
        only="temporal",
        labels=frozenset({"gta_iv_gameplay_ep003", "gta_iv_gameplay_ep004"}),
    )
    assert report.ok
    assert len(report.videos) == 2
    assert report.excluded_noncanonical_temporal == [
        f"gta_iv/{stem}.mp4" for stem in ("004", "004_A", "004_B", "004_C")
    ]
    (tmp_path / "gta_iv" / "unexpected.mp4").write_bytes(b"unknown")
    failed = await preflight_gta_corpus(
        tmp_path,
        only="temporal",
        labels=frozenset({"gta_iv_gameplay_ep003"}),
    )
    assert not failed.ok
    assert "unexpected_temporal_file" in {issue.code for issue in failed.issues}


async def test_preflight_reports_duplicate_sha_and_cross_provenance_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    corpus_tree(tmp_path)
    monkeypatch.setattr("app.services.gta_corpus.FFprobe", FakeProbe)
    screenshot = next((tmp_path / "gta_vi" / "Images" / "GTAVI_Screenshots").rglob("*.jpg"))
    artwork = next(
        (tmp_path / "gta_vi" / "Images" / "GTAVI_Artwork_Wallpapers").rglob("*_landscape.jpg")
    )
    artwork.write_bytes(screenshot.read_bytes())
    report = await preflight_gta_corpus(tmp_path, smoke="initial")
    assert {item.code for item in report.issues} >= {
        "duplicate_sha",
        "cross_provenance_sha_conflict",
    }
    assert not report.ok


async def test_preflight_rejects_symlinks(monkeypatch, tmp_path: Path) -> None:
    corpus_tree(tmp_path)
    monkeypatch.setattr("app.services.gta_corpus.FFprobe", FakeProbe)
    image = next((tmp_path / "gta_vi" / "Images" / "GTAVI_Screenshots").rglob("*.jpg"))
    linked = image.parent / "linked.jpg"
    linked.symlink_to(image)
    linked.with_suffix(".md").write_text("description")
    report = await preflight_gta_corpus(tmp_path, smoke="initial")
    assert "unexpected_symlink" in {item.code for item in report.issues}


async def test_dry_run_never_constructs_an_api_client(monkeypatch, tmp_path: Path) -> None:
    report = CorpusPreflightReport(tmp_path, [], [], [])

    async def fake_preflight(*args, **kwargs):
        return report

    class ForbiddenClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("dry-run must not construct an API client")

    monkeypatch.setattr(gta_corpus_operator, "preflight_gta_corpus", fake_preflight)
    monkeypatch.setattr(gta_corpus_operator.httpx, "AsyncClient", ForbiddenClient)
    args = argparse.Namespace(
        project_id="project",
        corpus_root=tmp_path,
        api_url="http://test",
        dry_run=True,
        only="all",
        smoke=None,
        wait=False,
        concurrency=1,
        wait_timeout_seconds=1.0,
        json_report=None,
        ffprobe_binary="ffprobe",
    )
    assert await gta_corpus_operator.run(args) == 0


async def test_temporal_adapter_uses_canonical_endpoints_and_reuses_completed(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/sessions"):
            assert request.headers["Idempotency-Key"].startswith(
                "gta_canonical_corpus_operator_v1:gta_iv_gameplay_ep000:"
            )
            return httpx.Response(
                202,
                json={
                    "session": {"id": "session", "media_id": "media", "status": "ready"},
                    "job": {"id": "initial", "status": "completed"},
                },
            )
        if request.url.path == "/sessions/session" and request.method == "GET":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path.endswith("/timeline-notes/import"):
            body = request.read().decode()
            assert "operator_semantic_timeline_import" in body
            return httpx.Response(
                200, json={"imported_count": 0, "skipped_count": 1, "errors": None}
            )
        if request.url.path.endswith("/transcriptions"):
            return httpx.Response(
                202,
                json={
                    "transcription": {"id": "tx"},
                    "job": {"id": "tx-job", "status": "completed"},
                    "created": False,
                },
            )
        if request.url.path.endswith("/visual-indexes"):
            return httpx.Response(
                202,
                json={
                    "visual_index": {"id": "visual"},
                    "job": {"id": "visual-job", "status": "completed"},
                    "created": False,
                },
            )
        if request.url.path.endswith("/temporal-indexes"):
            return httpx.Response(
                202,
                json={
                    "temporal_index": {"id": "temporal"},
                    "job": {"id": "temporal-job", "status": "completed"},
                    "created": False,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    entry = VideoCorpusEntry(
        "gta_iv",
        "gta_iv_gameplay_ep000",
        tmp_path / "000.mkv",
        tmp_path / "000.md",
        sha256="a" * 64,
        observations=[{"start_seconds": 0, "end_seconds": 1, "description": "event", "tags": []}],
    )
    entry.path.write_bytes(b"video")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as client:
            status, reused = await gta_corpus_operator.ingest_temporal_entry(
                client, entry, "project", wait=False, timeout_seconds=1
            )
    finally:
        entry.path.unlink()
    assert status == "completed"
    assert reused
    assert [path for _, path in seen][-1] == "/sessions/session/temporal-indexes"


async def test_static_adapter_disables_vision_and_imports_existing_sidecar(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "one.jpg"
    image_path.write_bytes(image_bytes("red"))
    entry = ImageCorpusEntry(
        "screenshot",
        image_path,
        tmp_path / "one.md",
        {"source_owner": "rockstar_games"},
        sidecar_sha256="b" * 64,
        content_type="image/jpeg",
        semantic_text="Existing operator semantic text.",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images"):
            assert b'enqueue_semantic_index"\r\n\r\nfalse' in request.read()
            return httpx.Response(
                202,
                json={"media_asset": {"id": "media"}, "media_created": False},
            )
        if request.url.path.endswith("/operator-image-semantics"):
            body = request.read().decode()
            assert "human_assisted_import" in body
            assert "neovids-vision" not in body
            return httpx.Response(
                202,
                json={
                    "semantic_index": {"id": "semantic"},
                    "job": {"id": "job", "status": "completed"},
                    "created": False,
                },
            )
        raise AssertionError(f"unexpected request {request.url.path}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        status, reused = await gta_corpus_operator.ingest_static_entry(
            client, entry, "project", wait=False, timeout_seconds=1
        )
    assert status == "completed"
    assert reused


async def test_batch_counts_partial_failure_and_resume(monkeypatch, tmp_path: Path) -> None:
    videos = [
        VideoCorpusEntry("gta_iv", "one", tmp_path / "one", tmp_path / "one.md"),
        VideoCorpusEntry("gta_iv", "two", tmp_path / "two", tmp_path / "two.md"),
    ]
    report = CorpusPreflightReport(tmp_path, videos, [], [])

    async def fake_ingest(_client, entry, *args, **kwargs):
        if entry.label == "two":
            raise RuntimeError("partial failure")
        return "completed", True

    monkeypatch.setattr(gta_corpus_operator, "ingest_temporal_entry", fake_ingest)

    class ProjectClient:
        async def get(self, path: str) -> Any:
            return httpx.Response(200, request=httpx.Request("GET", f"http://test{path}"))

    counts = await gta_corpus_operator.execute_batch(
        ProjectClient(),
        report,
        project_id="project",
        wait=False,
        timeout_seconds=1,
        concurrency=1,
    )
    assert counts.completed == 1
    assert counts.reused == 1
    assert counts.failed == 1
