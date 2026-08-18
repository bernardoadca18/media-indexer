from uuid import UUID

from app.models import MediaSession, TranscriptSegment


async def test_transcription_api_creation_idempotency_and_segment_pagination(
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
        session.status = "ready"
        await db.commit()

    response = client.post(
        f"/sessions/{session_id}/transcriptions",
        json={"model": "small", "word_timestamps": True},
    )
    assert response.status_code == 202
    created = response.json()
    transcription_id = UUID(created["transcription"]["id"])
    assert created["created"] is True
    assert created["job"]["status"] == "pending"
    assert created["job"]["transcription_id"] == str(transcription_id)

    repeated = client.post(
        f"/sessions/{session_id}/transcriptions",
        json={"model": "small", "word_timestamps": True},
    )
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["transcription"]["id"] == str(transcription_id)
    assert repeated.json()["job"]["id"] == created["job"]["id"]

    async with session_factory() as db:
        db.add_all(
            [
                TranscriptSegment(
                    transcription_id=transcription_id,
                    sequence=1,
                    start_seconds=0.5,
                    end_seconds=1.25,
                    text="first",
                    word_timestamps=None,
                ),
                TranscriptSegment(
                    transcription_id=transcription_id,
                    sequence=2,
                    start_seconds=2.0,
                    end_seconds=3.5,
                    text="second",
                    word_timestamps=[
                        {
                            "word": "second",
                            "start": 2.0,
                            "end": 3.5,
                            "probability": 0.9,
                        }
                    ],
                ),
            ]
        )
        await db.commit()

    listing = client.get(f"/sessions/{session_id}/transcriptions")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [str(transcription_id)]

    detail = client.get(f"/transcriptions/{transcription_id}")
    assert detail.status_code == 200
    assert detail.json()["model"] == "small"

    first_page = client.get(
        f"/transcriptions/{transcription_id}/segments", params={"offset": 0, "limit": 1}
    )
    second_page = client.get(
        f"/transcriptions/{transcription_id}/segments", params={"offset": 1, "limit": 1}
    )
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert first_page.json()["items"][0]["sequence"] == 1
    assert second_page.json()["items"][0]["sequence"] == 2
    assert second_page.json()["items"][0]["word_timestamps"][0]["probability"] == 0.9


def test_transcription_requires_ready_session(client) -> None:
    project = client.post("/projects", json={"name": "Witcher"}).json()
    ingestion = client.post(
        f"/projects/{project['id']}/sessions",
        data={"label": "001"},
        files={
            "video": ("001.mp4", b"fake-video", "video/mp4"),
            "notes": ("001.txt", b"note", "text/plain"),
        },
    ).json()
    response = client.post(f"/sessions/{ingestion['session']['id']}/transcriptions", json={})
    assert response.status_code == 409
