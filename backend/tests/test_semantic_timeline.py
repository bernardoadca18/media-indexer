from uuid import uuid4

from sqlalchemy import event

from app.models import (
    AlignmentRun,
    AlignmentSubject,
    CandidateAlignment,
    DerivedAsset,
    MediaAsset,
    MediaProject,
    MediaSession,
    Note,
    RetrievalCandidate,
    RetrievalRun,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    TemporalSegmentFrameLink,
    TemporalSegmentSceneLink,
    TemporalSegmentTranscriptLink,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.services.semantic_timeline import SemanticTimelineService


async def seed_timeline(db):
    project = MediaProject(name=f"timeline-{uuid4()}")
    db.add(project)
    await db.flush()
    media = MediaAsset(
        project_id=project.id,
        storage_uri=f"local://timeline/{uuid4()}.mp4",
        original_filename="gameplay.mp4",
        content_type="video/mp4",
        byte_size=100,
        sha256="a" * 64,
        duration_seconds=120.0,
    )
    db.add(media)
    await db.flush()
    raw_notes = "  Aprendo o Aard...  \n\nsem correspondencia\n\nsem candidates"
    session = MediaSession(
        project_id=project.id,
        media_id=media.id,
        label="timeline",
        notes_filename="notes.txt",
        raw_notes=raw_notes,
        status="ready",
    )
    db.add(session)
    await db.flush()
    notes = [
        Note(session_id=session.id, order=1, raw="  Aprendo o Aard...  "),
        Note(session_id=session.id, order=2, raw="sem correspondencia"),
        Note(session_id=session.id, order=3, raw="sem candidates"),
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
        duration_seconds=120.0,
    )
    visual = VisualIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        status="completed",
        detector="fake",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add_all([*notes, transcription, visual])
    await db.flush()
    temporal = TemporalIndex(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        transcription_id=transcription.id,
        visual_index_id=visual.id,
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
        embedding_provider="fake",
        embedding_model="fake",
        embedding_dimensions=1024,
    )
    db.add(temporal)
    await db.flush()
    transcript_segments = [
        TranscriptSegment(
            transcription_id=transcription.id,
            sequence=1,
            start_seconds=10.0,
            end_seconds=25.0,
            text="Geralt aprende Aard",
        ),
        TranscriptSegment(
            transcription_id=transcription.id,
            sequence=2,
            start_seconds=95.0,
            end_seconds=110.0,
            text="combate",
        ),
    ]
    scenes = [
        SceneSegment(
            visual_index_id=visual.id,
            sequence=1,
            start_seconds=0.0,
            end_seconds=60.0,
            duration_seconds=60.0,
        ),
        SceneSegment(
            visual_index_id=visual.id,
            sequence=2,
            start_seconds=60.0,
            end_seconds=120.0,
            duration_seconds=60.0,
        ),
    ]
    frames = [
        DerivedAsset(
            project_id=project.id,
            session_id=session.id,
            media_id=media.id,
            visual_index_id=visual.id,
            kind="representative_frame",
            storage_uri=f"local://timeline/frame-{timestamp}.jpg",
            mime_type="image/jpeg",
            byte_size=10,
            sha256=f"{index:x}" * 64,
            width=640,
            height=360,
            timestamp_seconds=timestamp,
            asset_metadata={"origins": ["periodic"]},
        )
        for index, timestamp in enumerate((15.0, 75.0, 105.0), start=1)
    ]
    db.add_all([*transcript_segments, *scenes, *frames])
    await db.flush()
    segments = []
    texts = ["Geralt aprende Aard", "continua tutorial", "", "combate"]
    for sequence, text in enumerate(texts, start=1):
        start = (sequence - 1) * 30.0
        segment = TemporalSegment(
            temporal_index_id=temporal.id,
            sequence=sequence,
            start_seconds=start,
            end_seconds=start + 30.0,
            duration_seconds=30.0,
            text=text,
            search_text=text,
            segment_metadata={"boundary": "deterministic"},
            embedding=([float(sequence)] + [0.0] * 1023) if text else None,
            embedding_status="completed" if text else "not_applicable",
        )
        db.add(segment)
        segments.append(segment)
    await db.flush()
    db.add_all(
        [
            TemporalSegmentTranscriptLink(
                temporal_segment_id=segments[0].id,
                transcript_segment_id=transcript_segments[0].id,
            ),
            TemporalSegmentTranscriptLink(
                temporal_segment_id=segments[3].id,
                transcript_segment_id=transcript_segments[1].id,
            ),
            TemporalSegmentSceneLink(
                temporal_segment_id=segments[0].id,
                scene_segment_id=scenes[0].id,
            ),
            TemporalSegmentSceneLink(
                temporal_segment_id=segments[1].id,
                scene_segment_id=scenes[0].id,
            ),
            TemporalSegmentSceneLink(
                temporal_segment_id=segments[2].id,
                scene_segment_id=scenes[1].id,
            ),
            TemporalSegmentSceneLink(
                temporal_segment_id=segments[3].id,
                scene_segment_id=scenes[1].id,
            ),
            TemporalSegmentFrameLink(
                temporal_segment_id=segments[0].id,
                derived_asset_id=frames[0].id,
            ),
            TemporalSegmentFrameLink(
                temporal_segment_id=segments[2].id,
                derived_asset_id=frames[1].id,
            ),
            TemporalSegmentFrameLink(
                temporal_segment_id=segments[3].id,
                derived_asset_id=frames[2].id,
            ),
        ]
    )
    retrieval = RetrievalRun(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        temporal_index_id=temporal.id,
        kind="notes",
        strategy="semantic_temporal_v1",
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add(retrieval)
    await db.flush()
    candidate = RetrievalCandidate(
        retrieval_run_id=retrieval.id,
        note_id=notes[0].id,
        subject_key=f"note:{notes[0].id}",
        rank=1,
        start_seconds=20.0,
        end_seconds=70.0,
        duration_seconds=50.0,
        score=0.9,
        score_components={"semantic_max": 0.9},
    )
    db.add(candidate)
    await db.flush()
    alignment = AlignmentRun(
        project_id=project.id,
        session_id=session.id,
        media_id=media.id,
        retrieval_run_id=retrieval.id,
        provider="fake",
        model="fake-model",
        strategy="multimodal_alignment_v1",
        prompt_version="alignment_prompt_v1",
        status="completed",
        configuration={},
        configuration_fingerprint=uuid4().hex,
    )
    db.add(alignment)
    await db.flush()
    matched_subject = AlignmentSubject(
        alignment_run_id=alignment.id,
        subject_key=f"note:{notes[0].id}",
        note_id=notes[0].id,
        observation=notes[0].raw,
        status="matched",
    )
    unmatched_subject = AlignmentSubject(
        alignment_run_id=alignment.id,
        subject_key=f"note:{notes[1].id}",
        note_id=notes[1].id,
        observation=notes[1].raw,
        status="unmatched",
    )
    no_candidates_subject = AlignmentSubject(
        alignment_run_id=alignment.id,
        subject_key=f"note:{notes[2].id}",
        note_id=notes[2].id,
        observation=notes[2].raw,
        status="no_candidates",
    )
    db.add_all(
        [matched_subject, unmatched_subject, no_candidates_subject]
    )
    await db.flush()
    selected = CandidateAlignment(
        alignment_run_id=alignment.id,
        alignment_subject_id=matched_subject.id,
        retrieval_candidate_id=candidate.id,
        note_id=notes[0].id,
        retrieval_rank=1,
        status="matched",
        start_seconds=25.0,
        end_seconds=65.0,
        confidence=0.93,
        anchors=[
            {
                "timestamp_seconds": 42.0,
                "description": "Aard tutorial",
            }
        ],
        reasoning_summary="Aard tutorial is visible.",
        timestamp_resolution_seconds=2.0,
        pass_count=1,
    )
    db.add(selected)
    await db.flush()
    matched_subject.selected_candidate_alignment_id = selected.id
    await db.commit()
    return {
        "project": project,
        "session": session,
        "media": media,
        "notes": notes,
        "temporal": temporal,
        "segments": segments,
        "alignment": alignment,
        "selected": selected,
    }


async def test_projection_with_alignment_preserves_provenance_and_raw(
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        result = await SemanticTimelineService(db).get(
            session_id=domain["session"].id,
            temporal_index_id=domain["temporal"].id,
            alignment_run_id=domain["alignment"].id,
            start_seconds=None,
            end_seconds=None,
            offset=0,
            limit=10,
        )
        repeated = await SemanticTimelineService(db).get(
            session_id=domain["session"].id,
            temporal_index_id=domain["temporal"].id,
            alignment_run_id=domain["alignment"].id,
            start_seconds=None,
            end_seconds=None,
            offset=0,
            limit=10,
        )

        assert result == repeated
        assert result.total == 4
        assert [item.sequence for item in result.items] == [1, 2, 3, 4]
        assert result.items[2].silent is True
        assert result.items[2].text == ""
        assert result.notes[0].raw == "  Aprendo o Aard...  "
        assert [note.alignment_status for note in result.notes] == [
            "matched",
            "unmatched",
            "no_candidates",
        ]
        selected = result.notes[0].selected_alignment
        assert selected is not None
        assert (selected.start_seconds, selected.end_seconds) == (25.0, 65.0)
        assert selected.confidence == 0.93
        assert selected.anchors[0]["timestamp_seconds"] == 42.0
        assert selected.temporal_segment_ids == [
            segment.id for segment in domain["segments"][:3]
        ]
        assert domain["notes"][0].id in result.items[0].matched_note_ids
        assert domain["selected"].id in result.items[2].selected_alignment_ids
        assert result.provenance.alignment is not None
        assert (
            result.provenance.transcription.transcription_id
            == domain["temporal"].transcription_id
        )
        assert (
            result.provenance.visual_index.visual_index_id
            == domain["temporal"].visual_index_id
        )
        frame = result.items[0].evidence.frames[0]
        assert frame.storage_uri.startswith("local://")
        serialized = result.model_dump_json()
        assert "base64" not in serialized
        assert "embedding" not in serialized
        assert "[1.0" not in serialized


async def test_projection_without_alignment_is_explicit_and_paginated(
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        result = await SemanticTimelineService(db).get(
            session_id=domain["session"].id,
            temporal_index_id=domain["temporal"].id,
            alignment_run_id=None,
            start_seconds=None,
            end_seconds=None,
            offset=1,
            limit=2,
        )
        assert result.total == 4
        assert result.offset == 1 and result.limit == 2
        assert [item.sequence for item in result.items] == [2, 3]
        assert result.provenance.alignment is None
        assert all(
            note.alignment_status == "not_aligned" for note in result.notes
        )
        assert all(note.selected_alignment is None for note in result.notes)


async def test_projection_range_uses_half_open_overlap_semantics(
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        bounded = await SemanticTimelineService(db).get(
            session_id=domain["session"].id,
            temporal_index_id=domain["temporal"].id,
            alignment_run_id=None,
            start_seconds=60.0,
            end_seconds=90.0,
            offset=0,
            limit=10,
        )
        assert bounded.total == 1
        assert [item.sequence for item in bounded.items] == [3]
        assert bounded.range.semantics == "[start,end)"

        overlap = await SemanticTimelineService(db).get(
            session_id=domain["session"].id,
            temporal_index_id=domain["temporal"].id,
            alignment_run_id=None,
            start_seconds=55.0,
            end_seconds=95.0,
            offset=0,
            limit=10,
        )
        assert overlap.total == 3
        assert [item.sequence for item in overlap.items] == [2, 3, 4]


async def test_projection_query_count_is_fixed_not_per_item(
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        count = 0

        def before_cursor_execute(*args):
            nonlocal count
            count += 1

        connection = await db.connection()
        event.listen(
            connection.sync_connection.engine,
            "before_cursor_execute",
            before_cursor_execute,
        )
        try:
            await SemanticTimelineService(db).get(
                session_id=domain["session"].id,
                temporal_index_id=domain["temporal"].id,
                alignment_run_id=domain["alignment"].id,
                start_seconds=None,
                end_seconds=None,
                offset=0,
                limit=100,
            )
        finally:
            event.remove(
                connection.sync_connection.engine,
                "before_cursor_execute",
                before_cursor_execute,
            )
        assert count <= 10


async def test_semantic_timeline_api_pagination_range_and_errors(
    client,
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        session_id = domain["session"].id
        temporal_id = domain["temporal"].id
        alignment_id = domain["alignment"].id

        other_project = MediaProject(name="other")
        db.add(other_project)
        await db.flush()
        other_media = MediaAsset(
            project_id=other_project.id,
            storage_uri=f"local://timeline/{uuid4()}.mp4",
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
            raw_notes="other",
            status="ready",
        )
        db.add(other_session)
        await db.commit()
        other_session_id = other_session.id

    response = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={
            "temporal_index_id": str(temporal_id),
            "alignment_run_id": str(alignment_id),
            "start_seconds": 55,
            "end_seconds": 95,
            "offset": 1,
            "limit": 1,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert payload["offset"] == 1
    assert [item["sequence"] for item in payload["items"]] == [3]
    assert payload["notes"][0]["alignment_status"] == "matched"

    missing_session = client.get(
        f"/sessions/{uuid4()}/semantic-timeline",
        params={"temporal_index_id": str(temporal_id)},
    )
    assert missing_session.status_code == 404

    wrong_session = client.get(
        f"/sessions/{other_session_id}/semantic-timeline",
        params={"temporal_index_id": str(temporal_id)},
    )
    assert wrong_session.status_code == 409

    missing_index = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={"temporal_index_id": str(uuid4())},
    )
    assert missing_index.status_code == 404

    invalid_range = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={
            "temporal_index_id": str(temporal_id),
            "start_seconds": 90,
            "end_seconds": 60,
        },
    )
    assert invalid_range.status_code == 422


async def test_incomplete_index_and_incompatible_alignment_are_rejected(
    client,
    session_factory,
) -> None:
    async with session_factory() as db:
        domain = await seed_timeline(db)
        domain["temporal"].status = "processing"
        await db.commit()
        session_id = domain["session"].id
        temporal_id = domain["temporal"].id
        alignment_id = domain["alignment"].id

    incomplete = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={"temporal_index_id": str(temporal_id)},
    )
    assert incomplete.status_code == 409

    async with session_factory() as db:
        domain["alignment"].status = "processing"
        domain["temporal"].status = "completed"
        await db.commit()
        session_id = domain["session"].id
        temporal_id = domain["temporal"].id
        alignment_id = domain["alignment"].id

    incomplete_alignment = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={
            "temporal_index_id": str(temporal_id),
            "alignment_run_id": str(alignment_id),
        },
    )
    assert incomplete_alignment.status_code == 409

    async with session_factory() as db:
        domain["alignment"].status = "completed"
        incompatible_temporal = TemporalIndex(
            project_id=domain["temporal"].project_id,
            session_id=domain["temporal"].session_id,
            media_id=domain["temporal"].media_id,
            transcription_id=domain["temporal"].transcription_id,
            visual_index_id=domain["temporal"].visual_index_id,
            status="completed",
            configuration={"variant": "incompatible"},
            configuration_fingerprint=uuid4().hex,
            embedding_provider="fake",
            embedding_model="fake",
            embedding_dimensions=1024,
        )
        db.add(incompatible_temporal)
        await db.commit()
        incompatible_temporal_id = incompatible_temporal.id

    incompatible_alignment = client.get(
        f"/sessions/{session_id}/semantic-timeline",
        params={
            "temporal_index_id": str(incompatible_temporal_id),
            "alignment_run_id": str(alignment_id),
        },
    )
    assert incompatible_alignment.status_code == 409
