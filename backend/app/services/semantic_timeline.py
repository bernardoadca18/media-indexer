from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CandidateAlignment, MediaSession, Note, TemporalIndex, TemporalSegment
from app.repositories.semantic_timeline import (
    SemanticTimelineRepository,
    TimelineAlignmentContext,
    TimelineEvidenceMaps,
    TimelineNoteSelection,
    TimelineSelectedOverlap,
)
from app.schemas.semantic_timeline import (
    SemanticTimelineOut,
    TimelineAlignmentProvenanceOut,
    TimelineEvidenceOut,
    TimelineFrameRefOut,
    TimelineItemOut,
    TimelineNoteOut,
    TimelineProvenanceOut,
    TimelineRangeOut,
    TimelineSceneRefOut,
    TimelineSelectedAlignmentOut,
    TimelineTranscriptionProvenanceOut,
    TimelineTranscriptRefOut,
    TimelineVisualProvenanceOut,
)


class SemanticTimelinePrerequisiteError(ValueError):
    pass


class SemanticTimelineService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = SemanticTimelineRepository(db)

    async def get(
        self,
        *,
        session_id: UUID,
        temporal_index_id: UUID,
        alignment_run_id: UUID | None,
        start_seconds: float | None,
        end_seconds: float | None,
        offset: int,
        limit: int,
    ) -> SemanticTimelineOut:
        self._validate_range(start_seconds, end_seconds)
        context = await self.repository.context(session_id, temporal_index_id)
        if context is None:
            if await self.db.get(MediaSession, session_id) is None:
                raise LookupError("session not found")
            if await self.db.get(TemporalIndex, temporal_index_id) is None:
                raise LookupError("temporal index not found")
            raise SemanticTimelinePrerequisiteError("temporal index does not belong to the session")
        temporal_index = context.temporal_index
        if temporal_index.status != "completed":
            raise SemanticTimelinePrerequisiteError("temporal index must be completed")

        alignment_context: TimelineAlignmentContext | None = None
        if alignment_run_id is not None:
            alignment_context = await self.repository.alignment_context(alignment_run_id)
            if alignment_context is None:
                raise LookupError("alignment run not found")
            self._validate_alignment(
                alignment_context,
                session_id=session_id,
                temporal_index_id=temporal_index_id,
                media_id=context.session.media_id,
            )

        segments, total = await self.repository.segments(
            temporal_index_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            offset=offset,
            limit=limit,
        )
        segment_ids = [segment.id for segment in segments]
        evidence = await self.repository.evidence(segment_ids)
        notes = list(await self.repository.notes(session_id))

        selections: dict[UUID, TimelineNoteSelection] = {}
        overlaps: list[TimelineSelectedOverlap] = []
        if alignment_context is not None:
            for selection in await self.repository.note_selections(
                alignment_context.alignment_run.id
            ):
                assert selection.subject.note_id is not None
                selections[selection.subject.note_id] = selection
            overlaps = list(
                await self.repository.selected_overlaps(
                    temporal_index_id,
                    alignment_context.alignment_run.id,
                )
            )

        segments_by_alignment: dict[UUID, list[UUID]] = {}
        notes_by_segment: dict[UUID, list[UUID]] = {}
        alignments_by_segment: dict[UUID, list[UUID]] = {}
        for overlap in overlaps:
            segments_by_alignment.setdefault(overlap.candidate_alignment_id, []).append(
                overlap.temporal_segment_id
            )
            notes_by_segment.setdefault(overlap.temporal_segment_id, []).append(overlap.note_id)
            alignments_by_segment.setdefault(overlap.temporal_segment_id, []).append(
                overlap.candidate_alignment_id
            )

        return SemanticTimelineOut(
            provenance=TimelineProvenanceOut(
                project_id=context.session.project_id,
                session_id=context.session.id,
                media_id=context.session.media_id,
                temporal_index_id=temporal_index.id,
                temporal_index_fingerprint=(temporal_index.configuration_fingerprint),
                transcription=TimelineTranscriptionProvenanceOut(
                    transcription_id=temporal_index.transcription_id
                ),
                visual_index=TimelineVisualProvenanceOut(
                    visual_index_id=temporal_index.visual_index_id
                ),
                alignment=self._alignment_provenance(alignment_context),
            ),
            range=TimelineRangeOut(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            ),
            items=[
                self._item(
                    segment,
                    evidence,
                    notes_by_segment,
                    alignments_by_segment,
                )
                for segment in segments
            ],
            notes=[
                self._note(
                    note,
                    alignment_context,
                    selections.get(note.id),
                    segments_by_alignment,
                )
                for note in notes
            ],
            total=total,
            offset=offset,
            limit=limit,
        )

    @staticmethod
    def _validate_range(
        start_seconds: float | None,
        end_seconds: float | None,
    ) -> None:
        if start_seconds is not None and start_seconds < 0:
            raise ValueError("start_seconds must be nonnegative")
        if end_seconds is not None and end_seconds < 0:
            raise ValueError("end_seconds must be nonnegative")
        if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")

    @staticmethod
    def _validate_alignment(
        context: TimelineAlignmentContext,
        *,
        session_id: UUID,
        temporal_index_id: UUID,
        media_id: UUID,
    ) -> None:
        run = context.alignment_run
        retrieval = context.retrieval_run
        if run.status != "completed":
            raise SemanticTimelinePrerequisiteError("alignment run must be completed")
        if run.session_id != session_id or run.media_id != media_id:
            raise SemanticTimelinePrerequisiteError(
                "alignment run does not belong to the session media"
            )
        if retrieval.temporal_index_id != temporal_index_id:
            raise SemanticTimelinePrerequisiteError(
                "alignment run was produced from another temporal index"
            )

    @staticmethod
    def _alignment_provenance(
        context: TimelineAlignmentContext | None,
    ) -> TimelineAlignmentProvenanceOut | None:
        if context is None:
            return None
        run = context.alignment_run
        return TimelineAlignmentProvenanceOut(
            alignment_run_id=run.id,
            retrieval_run_id=run.retrieval_run_id,
            provider=run.provider,
            model=run.model,
            strategy=run.strategy,
            prompt_version=run.prompt_version,
            configuration_fingerprint=run.configuration_fingerprint,
        )

    @staticmethod
    def _item(
        segment: TemporalSegment,
        evidence: TimelineEvidenceMaps,
        notes_by_segment: dict[UUID, list[UUID]],
        alignments_by_segment: dict[UUID, list[UUID]],
    ) -> TimelineItemOut:
        frames: list[TimelineFrameRefOut] = []
        for frame in evidence.frames[segment.id]:
            if frame.timestamp_seconds is None:
                raise SemanticTimelinePrerequisiteError(
                    "timeline frame evidence requires a timestamp"
                )
            frames.append(
                TimelineFrameRefOut(
                    id=frame.id,
                    kind=frame.kind,
                    timestamp_seconds=frame.timestamp_seconds,
                    storage_uri=frame.storage_uri,
                    mime_type=frame.mime_type,
                    width=frame.width,
                    height=frame.height,
                    metadata=frame.asset_metadata,
                )
            )
        return TimelineItemOut(
            temporal_segment_id=segment.id,
            sequence=segment.sequence,
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            duration_seconds=segment.duration_seconds,
            text=segment.text,
            metadata=segment.segment_metadata,
            silent=not segment.text.strip(),
            evidence=TimelineEvidenceOut(
                transcript_segments=[
                    TimelineTranscriptRefOut(
                        id=item.id,
                        sequence=item.sequence,
                        start_seconds=item.start_seconds,
                        end_seconds=item.end_seconds,
                    )
                    for item in evidence.transcripts[segment.id]
                ],
                scenes=[
                    TimelineSceneRefOut(
                        id=item.id,
                        sequence=item.sequence,
                        start_seconds=item.start_seconds,
                        end_seconds=item.end_seconds,
                    )
                    for item in evidence.scenes[segment.id]
                ],
                frames=frames,
            ),
            matched_note_ids=notes_by_segment.get(segment.id, []),
            selected_alignment_ids=alignments_by_segment.get(segment.id, []),
        )

    @staticmethod
    def _note(
        note: Note,
        alignment_context: TimelineAlignmentContext | None,
        selection: TimelineNoteSelection | None,
        segments_by_alignment: dict[UUID, list[UUID]],
    ) -> TimelineNoteOut:
        if alignment_context is None:
            status = "not_aligned"
            subject_id = None
            selected_out = None
        elif selection is None:
            status = "not_in_alignment_run"
            subject_id = None
            selected_out = None
        else:
            status = selection.subject.status
            subject_id = selection.subject.id
            selected_out = SemanticTimelineService._selected(
                selection,
                segments_by_alignment,
            )
        return TimelineNoteOut(
            id=note.id,
            order=note.order,
            raw=note.raw,
            start_seconds=note.start_seconds,
            end_seconds=note.end_seconds,
            description=note.description,
            tags=note.tags,
            alignment_status=status,
            alignment_subject_id=subject_id,
            selected_alignment=selected_out,
        )

    @staticmethod
    def _selected(
        selection: TimelineNoteSelection,
        segments_by_alignment: dict[UUID, list[UUID]],
    ) -> TimelineSelectedAlignmentOut | None:
        selected: CandidateAlignment | None = selection.selected
        if selection.subject.status != "matched":
            return None
        if (
            selected is None
            or selected.status != "matched"
            or selected.start_seconds is None
            or selected.end_seconds is None
            or selected.confidence is None
        ):
            raise SemanticTimelinePrerequisiteError(
                "matched alignment subject has no valid selected result"
            )
        return TimelineSelectedAlignmentOut(
            candidate_alignment_id=selected.id,
            retrieval_candidate_id=selected.retrieval_candidate_id,
            retrieval_rank=selected.retrieval_rank,
            start_seconds=selected.start_seconds,
            end_seconds=selected.end_seconds,
            confidence=selected.confidence,
            anchors=selected.anchors,
            reasoning_summary=selected.reasoning_summary,
            timestamp_resolution_seconds=(selected.timestamp_resolution_seconds),
            temporal_segment_ids=segments_by_alignment.get(selected.id, []),
        )
