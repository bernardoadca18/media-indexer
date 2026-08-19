from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.dependencies import get_embedding_provider
from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    DerivedAsset,
    MediaAsset,
    MediaProject,
    MediaSession,
    Note,
    RetrievalCandidate,
    RetrievalCandidateTemporalSegment,
    RetrievalRun,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.repositories.temporal_index import TemporalIndexRepository, TemporalSearchHit
from app.services.candidate_retrieval import (
    CandidateEvidenceService,
    CandidateRetrievalConfig,
    CandidateRetrievalService,
    RetrievalPrerequisiteError,
    build_candidate_definitions,
)
from app.services.embedding_provider import EmbeddingProviderError


class FakeEmbeddingProvider:
    name = "fake"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.batches: list[list[str]] = []

    async def embed(
        self,
        texts,
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        assert model == "fake-model"
        assert expected_dimensions == EMBEDDING_VECTOR_DIMENSIONS
        self.batches.append(list(texts))
        if self.fail:
            raise EmbeddingProviderError("fake provider failure")
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "aard" in lowered:
                code = 1.0
            elif "combat" in lowered:
                code = 2.0
            elif "castle" in lowered:
                code = 3.0
            else:
                code = 4.0
            vectors.append([code] + [0.0] * (expected_dimensions - 1))
        return vectors


def segment(sequence: int, start: float, end: float, text: str = "text") -> TemporalSegment:
    return TemporalSegment(
        id=uuid4(),
        temporal_index_id=uuid4(),
        sequence=sequence,
        start_seconds=start,
        end_seconds=end,
        duration_seconds=end - start,
        text=text,
        search_text=text,
        embedding_status="completed",
    )


def hit(item: TemporalSegment, score: float) -> TemporalSearchHit:
    return TemporalSearchHit(item, score)


def test_candidate_grouping_expansion_clamping_and_distance() -> None:
    segments = [
        segment(1, 0.0, 30.0),
        segment(2, 30.0, 60.0),
        segment(3, 300.0, 330.0),
    ]
    config = CandidateRetrievalConfig(
        context_before_seconds=15.0,
        context_after_seconds=20.0,
        max_segment_gap_seconds=5.0,
        max_candidate_duration_seconds=120.0,
    )
    definitions = build_candidate_definitions(
        [hit(segments[1], 0.94), hit(segments[0], 0.85), hit(segments[2], 0.8)],
        360.0,
        config,
    )

    assert len(definitions) == 2
    assert (definitions[0].start_seconds, definitions[0].end_seconds) == (0.0, 80.0)
    assert [item.hit.segment.sequence for item in definitions[0].hits] == [2, 1]
    assert (definitions[1].start_seconds, definitions[1].end_seconds) == (
        285.0,
        350.0,
    )

    end_clamped = build_candidate_definitions(
        [hit(segment(4, 340.0, 360.0), 0.9)],
        360.0,
        config,
    )[0]
    assert end_clamped.end_seconds == 360.0


def test_max_duration_splits_without_losing_hits() -> None:
    hits = [
        hit(segment(index + 1, index * 30.0, (index + 1) * 30.0), 0.9 - index * 0.01)
        for index in range(4)
    ]
    definitions = build_candidate_definitions(
        hits,
        180.0,
        CandidateRetrievalConfig(
            context_before_seconds=0.0,
            context_after_seconds=0.0,
            max_candidate_duration_seconds=90.0,
        ),
    )
    assert len(definitions) == 2
    assert sum(len(item.hits) for item in definitions) == 4
    assert all(item.end_seconds - item.start_seconds <= 90.0 for item in definitions)


def test_threshold_count_score_order_and_weak_chronological_prior() -> None:
    strong_early = segment(1, 0.0, 30.0)
    weaker_late = segment(2, 90.0, 120.0)
    definitions = build_candidate_definitions(
        [hit(weaker_late, 0.80), hit(strong_early, 0.95)],
        120.0,
        CandidateRetrievalConfig(
            max_candidates=1,
            min_semantic_score=0.5,
            context_before_seconds=0,
            context_after_seconds=0,
        ),
        chronological_position=1.0,
    )
    assert len(definitions) == 1
    assert definitions[0].start_seconds == 0.0
    assert definitions[0].score_components["semantic_max"] == 0.95
    assert definitions[0].score_components["chronological_prior"] == 0.125

    assert (
        build_candidate_definitions(
            [hit(strong_early, 0.2)],
            120.0,
            CandidateRetrievalConfig(min_semantic_score=0.5),
        )
        == []
    )


async def seed_retrieval_domain(db):
    project = MediaProject(name=str(uuid4()))
    db.add(project)
    await db.flush()
    session_id, media_id = uuid4(), uuid4()
    media = MediaAsset(
        id=media_id,
        project_id=project.id,
        storage_uri=f"local://candidate/{media_id}.mp4",
        original_filename="gameplay.mp4",
        content_type="video/mp4",
        byte_size=1,
        sha256="a" * 64,
        duration_seconds=360.0,
    )
    db.add(media)
    await db.flush()
    session = MediaSession(
        id=session_id,
        project_id=project.id,
        media_id=media.id,
        label="session",
        notes_filename="notes.txt",
        raw_notes="Geralt learns Aard\n\ncombat begins",
        status="ready",
    )
    db.add(session)
    await db.flush()
    notes = [
        Note(session_id=session.id, order=1, raw="Geralt learns Aard"),
        Note(session_id=session.id, order=2, raw="combat begins"),
    ]
    transcription = Transcription(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        provider="fake",
        model="small",
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        duration_seconds=360.0,
    )
    visual_index = VisualIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        status="completed",
        detector="fake",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add_all([*notes, transcription, visual_index])
    await db.flush()
    temporal_index = TemporalIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        transcription_id=transcription.id,
        visual_index_id=visual_index.id,
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        embedding_provider="fake",
        embedding_model="fake-model",
        embedding_dimensions=EMBEDDING_VECTOR_DIMENSIONS,
    )
    db.add(temporal_index)
    await db.flush()
    temporal_segments = [
        TemporalSegment(
            temporal_index_id=temporal_index.id,
            sequence=1,
            start_seconds=0.0,
            end_seconds=30.0,
            duration_seconds=30.0,
            text="Kaer Morhen castle",
            search_text="Kaer Morhen castle",
            embedding=[3.0] + [0.0] * 1023,
            embedding_status="completed",
        ),
        TemporalSegment(
            temporal_index_id=temporal_index.id,
            sequence=2,
            start_seconds=30.0,
            end_seconds=60.0,
            duration_seconds=30.0,
            text="Geralt learns Aard sign",
            search_text="Geralt learns Aard sign",
            embedding=[1.0] + [0.0] * 1023,
            embedding_status="completed",
        ),
        TemporalSegment(
            temporal_index_id=temporal_index.id,
            sequence=3,
            start_seconds=60.0,
            end_seconds=90.0,
            duration_seconds=30.0,
            text="",
            search_text="",
            embedding=None,
            embedding_status="not_applicable",
        ),
        TemporalSegment(
            temporal_index_id=temporal_index.id,
            sequence=4,
            start_seconds=90.0,
            end_seconds=120.0,
            duration_seconds=30.0,
            text="combat begins",
            search_text="combat begins",
            embedding=[2.0] + [0.0] * 1023,
            embedding_status="completed",
        ),
    ]
    transcripts = [
        TranscriptSegment(
            transcription_id=transcription.id,
            sequence=1,
            start_seconds=35.0,
            end_seconds=55.0,
            text="Geralt learns Aard sign",
        ),
        TranscriptSegment(
            transcription_id=transcription.id,
            sequence=2,
            start_seconds=95.0,
            end_seconds=110.0,
            text="combat begins",
        ),
    ]
    scenes = [
        SceneSegment(
            visual_index_id=visual_index.id,
            sequence=1,
            start_seconds=0.0,
            end_seconds=90.0,
            duration_seconds=90.0,
        ),
        SceneSegment(
            visual_index_id=visual_index.id,
            sequence=2,
            start_seconds=90.0,
            end_seconds=360.0,
            duration_seconds=270.0,
        ),
    ]
    frames = [
        DerivedAsset(
            project_id=project.id,
            session_id=session.id,
            media_id=media.id,
            visual_index_id=visual_index.id,
            kind="representative_frame",
            storage_uri=f"local://candidate/frame-{timestamp}.jpg",
            mime_type="image/jpeg",
            byte_size=1,
            sha256=f"{index:x}" * 64,
            width=640,
            height=360,
            timestamp_seconds=timestamp,
        )
        for index, timestamp in enumerate([45.0, 75.0, 100.0], start=1)
    ]
    db.add_all([*temporal_segments, *transcripts, *scenes, *frames])
    await db.commit()
    return {
        "project": project,
        "session": session,
        "media": media,
        "notes": notes,
        "temporal_index": temporal_index,
        "segments": temporal_segments,
    }


def install_fake_search(monkeypatch, segments):
    async def fake_search(repository, temporal_index_id, query_embedding, limit):
        assert isinstance(repository, TemporalIndexRepository)
        assert limit >= 1
        code = int(query_embedding[0])
        if code == 1:
            values = [
                TemporalSearchHit(segments[1], 0.93),
                TemporalSearchHit(segments[3], 0.60),
            ]
        elif code == 2:
            values = [TemporalSearchHit(segments[3], 0.92)]
        elif code == 3:
            values = [TemporalSearchHit(segments[0], 0.90)]
        else:
            values = []
        return values[:limit]

    monkeypatch.setattr(TemporalIndexRepository, "search", fake_search)


async def test_query_retrieval_persistence_idempotency_and_evidence(
    session_factory,
    monkeypatch,
) -> None:
    provider = FakeEmbeddingProvider()
    async with session_factory() as db:
        domain = await seed_retrieval_domain(db)
        install_fake_search(monkeypatch, domain["segments"])
        service = CandidateRetrievalService(db, provider)
        config = CandidateRetrievalConfig(
            context_before_seconds=15.0,
            context_after_seconds=40.0,
        )
        first = await service.retrieve_query(
            domain["temporal_index"].id,
            "  Geralt   learns Aard ",
            config,
        )
        repeated = await service.retrieve_query(
            domain["temporal_index"].id,
            "Geralt learns Aard",
            config,
        )
        changed = await service.retrieve_query(
            domain["temporal_index"].id,
            "Geralt learns Aard",
            CandidateRetrievalConfig(context_before_seconds=5.0),
        )

        assert first.created
        assert not repeated.created
        assert repeated.run.id == first.run.id
        assert changed.run.id != first.run.id
        assert len(first.candidates) == 2
        assert [item.rank for item in first.candidates] == [1, 2]
        assert first.candidates[0].score > first.candidates[1].score
        assert len(provider.batches) == 2
        assert await db.scalar(select(func.count()).select_from(RetrievalRun)) == 2
        assert await db.scalar(select(func.count()).select_from(RetrievalCandidate)) == 4

        evidence = await CandidateEvidenceService(db).get(first.candidates[0].id)
        assert [item.segment.sequence for item in evidence.contributing_hits] == [2]
        assert [item.sequence for item in evidence.window_segments] == [1, 2, 3, 4]
        assert evidence.window_segments[2].embedding_status == "not_applicable"
        assert [item.text for item in evidence.transcript_segments] == [
            "Geralt learns Aard sign",
            "combat begins",
        ]
        assert [item.timestamp_seconds for item in evidence.frames] == [45.0, 75.0]
        assert evidence.scenes
        assert (
            await db.scalar(select(func.count()).select_from(RetrievalCandidateTemporalSegment))
            == 4
        )


async def test_note_batch_preserves_raw_batches_embeddings_and_rejects_foreign_note(
    session_factory,
    monkeypatch,
) -> None:
    provider = FakeEmbeddingProvider()
    async with session_factory() as db:
        domain = await seed_retrieval_domain(db)
        install_fake_search(monkeypatch, domain["segments"])
        raw_values = [note.raw for note in domain["notes"]]
        result = await CandidateRetrievalService(db, provider).retrieve_notes(
            domain["temporal_index"].id,
            None,
            CandidateRetrievalConfig(),
        )

        assert provider.batches == [["Geralt learns Aard", "combat begins"]]
        assert {candidate.note_id for candidate in result.candidates} == {
            note.id for note in domain["notes"]
        }
        assert [note.raw for note in domain["notes"]] == raw_values
        assert all(
            candidate.score_components["chronological_prior"] is not None
            for candidate in result.candidates
        )

        other_project = MediaProject(name="other")
        db.add(other_project)
        await db.flush()
        other_media = MediaAsset(
            project_id=other_project.id,
            storage_uri=f"local://candidate/{uuid4()}.mp4",
            original_filename="other.mp4",
            byte_size=1,
            sha256="b" * 64,
        )
        db.add(other_media)
        await db.flush()
        other_session = MediaSession(
            project_id=other_project.id,
            media_id=other_media.id,
            label="other",
            notes_filename="other.txt",
            raw_notes="foreign",
            status="ready",
        )
        db.add(other_session)
        await db.flush()
        foreign_note = Note(session_id=other_session.id, order=1, raw="foreign")
        db.add(foreign_note)
        await db.commit()

        with pytest.raises(RetrievalPrerequisiteError, match="belong"):
            await CandidateRetrievalService(db, provider).retrieve_notes(
                domain["temporal_index"].id,
                [foreign_note.id],
                CandidateRetrievalConfig(),
            )


async def test_provider_failure_persists_failed_run_and_retry_rebuilds(
    session_factory,
    monkeypatch,
) -> None:
    async with session_factory() as db:
        domain = await seed_retrieval_domain(db)
        install_fake_search(monkeypatch, domain["segments"])
        config = CandidateRetrievalConfig()
        with pytest.raises(EmbeddingProviderError):
            await CandidateRetrievalService(
                db,
                FakeEmbeddingProvider(fail=True),
            ).retrieve_query(domain["temporal_index"].id, "Geralt learns Aard", config)

        run = (await db.execute(select(RetrievalRun))).scalar_one()
        assert run.status == "failed"
        assert await db.scalar(select(func.count()).select_from(RetrievalCandidate)) == 0

        retry = await CandidateRetrievalService(
            db,
            FakeEmbeddingProvider(),
        ).retrieve_query(domain["temporal_index"].id, "Geralt learns Aard", config)
        assert not retry.created
        assert retry.run.id == run.id
        assert retry.run.status == "completed"
        assert len(retry.candidates) == 2
        assert len({candidate.id for candidate in retry.candidates}) == 2


async def test_candidate_retrieval_api_contracts(
    client,
    session_factory,
    monkeypatch,
) -> None:
    provider = FakeEmbeddingProvider()
    async with session_factory() as db:
        domain = await seed_retrieval_domain(db)
        temporal_index_id = domain["temporal_index"].id
        note_id = domain["notes"][0].id
        install_fake_search(monkeypatch, domain["segments"])

    client.app.dependency_overrides[get_embedding_provider] = lambda: provider
    payload = {
        "query": "Geralt learns Aard",
        "context_before_seconds": 15.0,
        "context_after_seconds": 40.0,
    }
    created = client.post(
        f"/temporal-indexes/{temporal_index_id}/candidates",
        json=payload,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["created"] is True
    assert body["retrieval_run"]["kind"] == "query"
    assert [item["rank"] for item in body["candidates"]] == [1, 2]
    assert body["candidates"][0]["score"] > body["candidates"][1]["score"]
    run_id = body["retrieval_run"]["id"]
    candidate_id = body["candidates"][0]["id"]

    repeated = client.post(
        f"/temporal-indexes/{temporal_index_id}/candidates",
        json=payload,
    )
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["retrieval_run"]["id"] == run_id

    detail = client.get(f"/retrieval-runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["candidates"][0]["id"] == candidate_id
    listing = client.get(f"/retrieval-runs/{run_id}/candidates")
    assert listing.status_code == 200
    assert len(listing.json()) == 2

    evidence = client.get(f"/retrieval-candidates/{candidate_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    evidence_body = evidence.json()
    assert evidence_body["note"] is None
    assert evidence_body["contributing_hits"][0]["sequence"] == 2
    assert evidence_body["temporal_segments"][2]["text"] == ""
    assert evidence_body["frames"][0]["storage_uri"].startswith("local://")

    single_note = client.post(
        f"/temporal-indexes/{temporal_index_id}/notes/{note_id}/candidates",
        json={},
    )
    assert single_note.status_code == 200, single_note.text
    assert single_note.json()["retrieval_run"]["kind"] == "notes"
    assert {item["note_id"] for item in single_note.json()["candidates"]} == {str(note_id)}

    batch = client.post(
        f"/temporal-indexes/{temporal_index_id}/note-retrievals",
        json={},
    )
    assert batch.status_code == 200, batch.text
    assert len({item["note_id"] for item in batch.json()["candidates"]}) == 2

    no_results = client.post(
        f"/temporal-indexes/{temporal_index_id}/candidates",
        json={"query": "unknown retrieval phrase"},
    )
    assert no_results.status_code == 200
    assert no_results.json()["candidates"] == []

    missing = client.post(
        f"/temporal-indexes/{uuid4()}/candidates",
        json={"query": "Aard"},
    )
    assert missing.status_code == 404

    async with session_factory() as db:
        temporal_index = await db.get(TemporalIndex, temporal_index_id)
        assert temporal_index is not None
        temporal_index.status = "failed"
        await db.commit()
    incomplete = client.post(
        f"/temporal-indexes/{temporal_index_id}/candidates",
        json={"query": "different Aard query"},
    )
    assert incomplete.status_code == 409
    assert "must be completed" in incomplete.json()["detail"]
