import argparse
from pathlib import Path

import pytest

from scripts import batch_ingest_images


def test_batch_scan_filters_extensions_and_symlinks(tmp_path: Path) -> None:
    (tmp_path / "one.jpg").write_bytes(b"x")
    (tmp_path / "two.PNG").write_bytes(b"x")
    (tmp_path / "ignore.gif").write_bytes(b"x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "three.webp").write_bytes(b"x")
    (tmp_path / "linked.jpg").symlink_to(tmp_path / "one.jpg")
    assert [path.name for path in batch_ingest_images.scan(tmp_path)] == [
        "three.webp",
        "one.jpg",
        "two.PNG",
    ]


async def test_batch_reports_partial_failure_and_reuse(monkeypatch, tmp_path: Path, capsys) -> None:
    paths = [tmp_path / name for name in ("a.jpg", "b.jpg", "c.jpg")]
    outcomes = iter([("created", "completed"), ("reused", "pending"), ("failed", "failed")])

    async def fake_ingest(*args, **kwargs):
        return next(outcomes)

    monkeypatch.setattr(batch_ingest_images, "scan", lambda directory: paths)
    monkeypatch.setattr(batch_ingest_images, "ingest_one", fake_ingest)
    args = argparse.Namespace(
        directory=tmp_path,
        project_id="project",
        api_url="http://test",
        source_owner=None,
        source_class=None,
        asset_class=None,
        collection=None,
        game=None,
        declared_metadata="{}",
        concurrency=1,
        wait=False,
        wait_timeout_seconds=1.0,
    )
    assert await batch_ingest_images.run(args) == 1
    report = capsys.readouterr().out
    assert "Found: 3" in report
    assert "Created: 1" in report
    assert "Reused: 1" in report
    assert "Pending: 1" in report
    assert "Failed: 1" in report


async def test_batch_concurrency_is_bounded(tmp_path: Path) -> None:
    args = argparse.Namespace(
        directory=tmp_path,
        project_id="project",
        api_url="http://test",
        source_owner=None,
        source_class=None,
        asset_class=None,
        collection=None,
        game=None,
        declared_metadata="{}",
        concurrency=9,
        wait=False,
        wait_timeout_seconds=1.0,
    )
    with pytest.raises(ValueError, match="between 1 and 8"):
        await batch_ingest_images.run(args)
