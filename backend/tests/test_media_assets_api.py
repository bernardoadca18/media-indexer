import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import get_storage
from app.models import MediaAsset
from app.services.storage import LocalStorage


async def _ingest(client):
    project = (await client.post("/projects", json={"name": "Composer source"})).json()
    response = await client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "source"},
        files={
            "video": ("gameplay.mp4", b"0123456789", "video/mp4"),
            "notes": ("gameplay.txt", b"note", "text/plain"),
        },
    )
    assert response.status_code == 202
    return response.json()["session"]["media"]


async def _set_managed_location(
    session_factory,
    media_id: str,
    storage_uri: str,
    content: bytes | None = None,
) -> str:
    async with session_factory() as db:
        item = await db.get(MediaAsset, UUID(media_id))
        assert item is not None
        item.storage_uri = storage_uri
        if content is not None:
            item.byte_size = len(content)
            item.sha256 = hashlib.sha256(content).hexdigest()
        await db.commit()
        return item.sha256


async def test_media_asset_metadata_content_range_and_safety(client) -> None:
    media = await _ingest(client)

    response = await client.get(f"/media-assets/{media['id']}")

    assert response.status_code == 200
    assert response.json()["sha256"] == media["sha256"]
    assert response.json()["byte_size"] == 10
    assert response.json()["original_filename"] == "gameplay.mp4"
    assert "storage_uri" not in response.json()
    assert "probe_metadata" not in response.json()

    full = await client.get(f"/media-assets/{media['id']}/content")
    partial = await client.get(f"/media-assets/{media['id']}/content", headers={"Range": "bytes=2-5"})

    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["x-content-sha256"] == media["sha256"]
    assert full.headers["etag"] == f'"{media["sha256"]}"'
    assert full.headers["accept-ranges"] == "bytes"
    assert partial.status_code == 206
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == "bytes 2-5/10"
    media_id = uuid4()

    assert (await client.get(f"/media-assets/{media_id}")).status_code == 404
    assert (await client.get(f"/media-assets/{media_id}/content")).status_code == 404
    assert (await client.get("/media-assets/../../etc/passwd/content")).status_code == 404


async def test_media_asset_content_materializes_local_hdd(
    client, session_factory, tmp_path: Path
) -> None:
    media = await _ingest(client)
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()
    relative = "projects/test/sessions/test/source/test.mp4"
    physical = hdd / relative
    physical.parent.mkdir(parents=True)
    content = b"managed hdd content"
    physical.write_bytes(content)
    expected_sha256 = await _set_managed_location(
        session_factory,
        media["id"],
        f"local-hdd://{relative}",
        content,
    )
    client.app.dependency_overrides[get_storage] = lambda: LocalStorage(
        primary, root_hdd=hdd, validate_hdd_mount=False
    )

    response = await client.get(f"/media-assets/{media['id']}/content")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["etag"] == f'"{expected_sha256}"'
    assert response.headers["x-content-sha256"] == expected_sha256
    assert response.headers["accept-ranges"] == "bytes"


async def test_media_asset_content_returns_404_for_missing_local_hdd(
    client, session_factory, tmp_path: Path
) -> None:
    media = await _ingest(client)
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()
    await _set_managed_location(
        session_factory,
        media["id"],
        "local-hdd://projects/test/sessions/test/source/missing.mp4",
    )
    client.app.dependency_overrides[get_storage] = lambda: LocalStorage(
        primary, root_hdd=hdd, validate_hdd_mount=False
    )

    response = await client.get(f"/media-assets/{media['id']}/content")

    assert response.status_code == 404
    assert response.json() == {"detail": "Managed media content unavailable"}


async def test_media_asset_content_returns_404_when_hdd_is_not_configured(
    client, session_factory, tmp_path: Path
) -> None:
    media = await _ingest(client)
    await _set_managed_location(
        session_factory,
        media["id"],
        "local-hdd://projects/test/sessions/test/source/test.mp4",
    )
    client.app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)

    response = await client.get(f"/media-assets/{media['id']}/content")

    assert response.status_code == 404
    assert response.json() == {"detail": "Managed media content unavailable"}


@pytest.mark.parametrize(
    "storage_uri",
    [
        "file:///etc/passwd",
        "local://../../outside",
        "local-hdd://../../outside",
    ],
)
async def test_media_asset_content_rejects_unmanaged_or_escaping_uri(
    client, session_factory, tmp_path: Path, storage_uri: str
) -> None:
    media = await _ingest(client)
    primary = tmp_path / "primary"
    hdd = tmp_path / "hdd"
    primary.mkdir()
    hdd.mkdir()
    await _set_managed_location(session_factory, media["id"], storage_uri)
    client.app.dependency_overrides[get_storage] = lambda: LocalStorage(
        primary, root_hdd=hdd, validate_hdd_mount=False
    )

    response = await client.get(f"/media-assets/{media['id']}/content")

    assert response.status_code == 404
    assert response.json() == {"detail": "Managed media content unavailable"}
