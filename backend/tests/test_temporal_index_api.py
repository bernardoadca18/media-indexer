from uuid import UUID, uuid4

from app.api.dependencies import get_embedding_provider
from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    DerivedAsset,
    MediaAsset,
    MediaSession,
    SceneSegment,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.repositories.temporal_index import TemporalIndexRepository, TemporalSearchHit
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.temporal_index import TemporalIndexProcessor, hybrid_semantic_score


class ApiEmbeddingProvider:
    name = "ollama"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        assert expected_dimensions == EMBEDDING_VECTOR_DIMENSIONS
        return [[1.0] + [0.0] * (expected_dimensions - 1) for _ in texts]


async def seed_completed_inputs(client, session_factory):
    project = (await client.post("/projects", json={"name": "Temporal API"})).json()
    ingestion = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            data={"label": "001"},
            files={
                "video": ("001.mp4", b"fake-video", "video/mp4"),
                "notes": ("001.txt", b"note", "text/plain"),
            },
        )
    ).json()
    session_id = UUID(ingestion["session"]["id"])
    async with session_factory() as db:
        session = await db.get(MediaSession, session_id)
        assert session is not None
        media = await db.get(MediaAsset, session.media_id)
        assert media is not None
        media.duration_seconds = 60.0
        session.status = "ready"
        transcription = Transcription(
            project_id=session.project_id,
            session_id=session.id,
            media_id=session.media_id,
            provider="fake-asr",
            model="small",
            status="completed",
            configuration={},
            configuration_fingerprint=uuid4().hex,
            duration_seconds=60.0,
        )
        visual_index = VisualIndex(
            project_id=session.project_id,
            session_id=session.id,
            media_id=session.media_id,
            status="completed",
            detector="fake",
            configuration={},
            configuration_fingerprint=uuid4().hex,
        )
        db.add_all([transcription, visual_index])
        await db.flush()
        db.add_all(
            [
                TranscriptSegment(
                    transcription_id=transcription.id,
                    sequence=1,
                    start_seconds=5.0,
                    end_seconds=20.0,
                    text="protect the laboratory",
                ),
                SceneSegment(
                    visual_index_id=visual_index.id,
                    sequence=1,
                    start_seconds=0.0,
                    end_seconds=30.0,
                    duration_seconds=30.0,
                ),
                SceneSegment(
                    visual_index_id=visual_index.id,
                    sequence=2,
                    start_seconds=30.0,
                    end_seconds=60.0,
                    duration_seconds=30.0,
                ),
                DerivedAsset(
                    project_id=session.project_id,
                    session_id=session.id,
                    media_id=session.media_id,
                    visual_index_id=visual_index.id,
                    kind="representative_frame",
                    storage_uri=f"local://api-frame/{visual_index.id}.jpg",
                    mime_type="image/jpeg",
                    byte_size=4,
                    sha256="c" * 64,
                    width=640,
                    height=360,
                    timestamp_seconds=10.0,
                ),
            ]
        )
        await db.commit()
        return session.id, transcription.id, visual_index.id


async def test_temporal_index_api_idempotency_pagination_and_search(
    client,
    session_factory,
    monkeypatch,
) -> None:
    session_id, transcription_id, visual_index_id = await seed_completed_inputs(
        client,
        session_factory,
    )
    payload = {
        "transcription_id": str(transcription_id),
        "visual_index_id": str(visual_index_id),
    }
    response = await client.post(f"/sessions/{session_id}/temporal-indexes", json=payload)
    assert response.status_code == 202
    created = response.json()
    temporal_index_id = UUID(created["temporal_index"]["id"])
    job_id = UUID(created["job"]["id"])
    assert created["created"] is True
    assert created["job"]["temporal_index_id"] == str(temporal_index_id)
    assert created["temporal_index"]["embedding_provider"] == "ollama"
    assert created["temporal_index"]["embedding_model"] == "mxbai-embed-large:latest"
    assert created["temporal_index"]["embedding_dimensions"] == 1024

    repeated = await client.post(f"/sessions/{session_id}/temporal-indexes", json=payload)
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["temporal_index"]["id"] == str(temporal_index_id)
    assert repeated.json()["job"]["id"] == str(job_id)

    provider = ApiEmbeddingProvider()
    async with session_factory() as db:
        registry = ProcessorRegistry(
            {
                "temporal_index": lambda inner_db: TemporalIndexProcessor(
                    inner_db,
                    provider,
                    batch_size=32,
                )
            }
        )
        assert await JobExecutor(db, registry).execute(job_id)
        records, total = await TemporalIndexRepository(db).segments(
            temporal_index_id,
            0,
            100,
        )
        assert total == 2
        first_segment = records[0].segment

    listing = await client.get(f"/sessions/{session_id}/temporal-indexes")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [str(temporal_index_id)]

    detail = await client.get(f"/temporal-indexes/{temporal_index_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["transcription_id"] == str(transcription_id)
    assert detail.json()["visual_index_id"] == str(visual_index_id)

    page = await client.get(
        f"/temporal-indexes/{temporal_index_id}/segments",
        params={"offset": 0, "limit": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["text"] == "protect the laboratory"
    assert page.json()["items"][0]["transcript_segment_ids"]
    assert "embedding" not in page.json()["items"][0]

    async def fake_search(
        repository,
        scoped_index_id,
        query_embedding,
        limit,
    ):
        assert isinstance(repository, TemporalIndexRepository)
        assert scoped_index_id == temporal_index_id
        assert query_embedding[0] == 1.0
        assert limit == 15
        return [TemporalSearchHit(first_segment, 0.91)]

    monkeypatch.setattr(TemporalIndexRepository, "search", fake_search)
    client.app.dependency_overrides[get_embedding_provider] = lambda: provider
    search = await client.post(
        f"/temporal-indexes/{temporal_index_id}/search",
        json={"query": "defend the lab", "limit": 5},
    )
    assert search.status_code == 200
    assert search.json()["items"] == [
        {
            "temporal_segment_id": str(first_segment.id),
            "start_seconds": first_segment.start_seconds,
            "end_seconds": first_segment.end_seconds,
            "score": hybrid_semantic_score(
                "defend the lab", first_segment.search_text, 0.91
            ),
        }
    ]
    assert provider.calls[-1] == ["defend the lab"]


async def test_temporal_index_api_requires_completed_explicit_inputs(
    client,
    session_factory,
) -> None:
    session_id, transcription_id, visual_index_id = await seed_completed_inputs(
        client,
        session_factory,
    )
    async with session_factory() as db:
        visual_index = await db.get(VisualIndex, visual_index_id)
        assert visual_index is not None
        visual_index.status = "failed"
        await db.commit()

    response = await client.post(
        f"/sessions/{session_id}/temporal-indexes",
        json={
            "transcription_id": str(transcription_id),
            "visual_index_id": str(visual_index_id),
        },
    )
    assert response.status_code == 409
    assert "visual index must be completed" in response.json()["detail"]
