from uuid import uuid4


def _ingest(client):
    project = client.post("/projects", json={"name": "Composer source"}).json()
    response = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "source"},
        files={
            "video": ("gameplay.mp4", b"0123456789", "video/mp4"),
            "notes": ("gameplay.txt", b"note", "text/plain"),
        },
    )
    assert response.status_code == 202
    return response.json()["session"]["media"]


def test_media_asset_metadata_content_range_and_safety(client) -> None:
    media = _ingest(client)

    response = client.get(f"/media-assets/{media['id']}")

    assert response.status_code == 200
    assert response.json()["sha256"] == media["sha256"]
    assert response.json()["byte_size"] == 10
    assert response.json()["original_filename"] == "gameplay.mp4"
    assert "storage_uri" not in response.json()
    assert "probe_metadata" not in response.json()

    full = client.get(f"/media-assets/{media['id']}/content")
    partial = client.get(f"/media-assets/{media['id']}/content", headers={"Range": "bytes=2-5"})

    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["x-content-sha256"] == media["sha256"]
    assert full.headers["accept-ranges"] == "bytes"
    assert partial.status_code == 206
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == "bytes 2-5/10"
    media_id = uuid4()

    assert client.get(f"/media-assets/{media_id}").status_code == 404
    assert client.get(f"/media-assets/{media_id}/content").status_code == 404
    assert client.get("/media-assets/../../etc/passwd/content").status_code == 404
