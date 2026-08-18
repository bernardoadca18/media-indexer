from uuid import UUID

from app.models import DerivedAsset, MediaAsset, MediaSession, SceneSegment, VisualIndex


async def test_visual_index_api_creation_idempotency_and_pagination(
    client, session_factory
) -> None:
    project = client.post("/projects", json={"name": "Witcher"}).json()
    ingestion = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "001"},
        files={
            "video": ("001.mp4", b"fake-video", "video/mp4"),
            "notes": ("001.txt", b"note", "text/plain"),
        },
    ).json()
    session_id = UUID(ingestion["session"]["id"])

    async with session_factory() as db:
        session = await db.get(MediaSession, session_id)
        assert session is not None
        media = await db.get(MediaAsset, session.media_id)
        assert media is not None
        media.duration_seconds = 30.0
        session.status = "ready"
        await db.commit()

    payload = {
        "scene_threshold": 10.0,
        "periodic_frame_interval_seconds": 10.0,
        "frame_width": 640,
        "dedup_tolerance_seconds": 0.5,
    }
    response = client.post(f"/sessions/{session_id}/visual-indexes", json=payload)
    assert response.status_code == 202
    created = response.json()
    visual_index_id = UUID(created["visual_index"]["id"])
    assert created["created"] is True
    assert created["job"]["status"] == "pending"
    assert created["job"]["visual_index_id"] == str(visual_index_id)

    repeated = client.post(f"/sessions/{session_id}/visual-indexes", json=payload)
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["visual_index"]["id"] == str(visual_index_id)
    assert repeated.json()["job"]["id"] == created["job"]["id"]

    async with session_factory() as db:
        visual_index = await db.get(VisualIndex, visual_index_id)
        assert visual_index is not None
        db.add_all(
            [
                SceneSegment(
                    visual_index_id=visual_index_id,
                    sequence=1,
                    start_seconds=0.0,
                    end_seconds=10.0,
                    duration_seconds=10.0,
                    detector_score=18.0,
                    segment_metadata={"cut": "opening"},
                ),
                SceneSegment(
                    visual_index_id=visual_index_id,
                    sequence=2,
                    start_seconds=10.0,
                    end_seconds=30.0,
                    duration_seconds=20.0,
                    detector_score=22.0,
                    segment_metadata={"cut": "gameplay"},
                ),
                DerivedAsset(
                    project_id=visual_index.project_id,
                    session_id=session_id,
                    media_id=visual_index.media_id,
                    visual_index_id=visual_index_id,
                    kind="representative_frame",
                    storage_uri=f"local://visual/{visual_index_id}/frame-1.jpg",
                    mime_type="image/jpeg",
                    byte_size=4,
                    sha256="a" * 64,
                    width=640,
                    height=360,
                    timestamp_seconds=5.0,
                    asset_metadata={"origins": ["scene"]},
                ),
                DerivedAsset(
                    project_id=visual_index.project_id,
                    session_id=session_id,
                    media_id=visual_index.media_id,
                    visual_index_id=visual_index_id,
                    kind="representative_frame",
                    storage_uri=f"local://visual/{visual_index_id}/frame-2.jpg",
                    mime_type="image/jpeg",
                    byte_size=4,
                    sha256="b" * 64,
                    width=640,
                    height=360,
                    timestamp_seconds=20.0,
                    asset_metadata={"origins": ["periodic", "scene"]},
                ),
            ]
        )
        await db.commit()

    listing = client.get(f"/sessions/{session_id}/visual-indexes")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [str(visual_index_id)]

    detail = client.get(f"/visual-indexes/{visual_index_id}")
    assert detail.status_code == 200
    assert detail.json()["detector"] == "ffmpeg_scdet"

    scenes = client.get(
        f"/visual-indexes/{visual_index_id}/scenes", params={"offset": 1, "limit": 1}
    )
    assert scenes.status_code == 200
    assert scenes.json()["total"] == 2
    assert scenes.json()["items"][0]["sequence"] == 2
    assert scenes.json()["items"][0]["metadata"] == {"cut": "gameplay"}

    frames = client.get(
        f"/visual-indexes/{visual_index_id}/frames", params={"offset": 1, "limit": 1}
    )
    assert frames.status_code == 200
    assert frames.json()["total"] == 2
    assert frames.json()["items"][0]["timestamp_seconds"] == 20.0
    assert frames.json()["items"][0]["metadata"]["origins"] == ["periodic", "scene"]
    assert "frames" not in ingestion["session"]
    assert "scenes" not in ingestion["session"]


def test_visual_index_requires_ready_session(client) -> None:
    project = client.post("/projects", json={"name": "Witcher"}).json()
    ingestion = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "001"},
        files={
            "video": ("001.mp4", b"fake-video", "video/mp4"),
            "notes": ("001.txt", b"note", "text/plain"),
        },
    ).json()
    response = client.post(f"/sessions/{ingestion['session']['id']}/visual-indexes", json={})
    assert response.status_code == 409
