# Domain model

- MediaProject groups sessions.
- MediaAsset owns logical URI, filename, checksum, size and probe metadata.
- MediaSession binds a project, primary media and the exact note document.
- Note is an ordered immutable unit parsed from that document.
- ProcessingJob persists status, stage, progress and error.

Consumers use media_id plus future temporal coordinates and never interpret
storage_uri. A repeated project-scoped Idempotency-Key returns the existing
session and job.

## Transcription

- Transcription represents one provider/configuration execution for a
  MediaAsset. It records project, session and media identity; provider and
  model; requested/detected language; lifecycle timestamps; duration; provider
  metadata; configuration and its fingerprint.
- TranscriptSegment belongs to one Transcription and records a unique sequence,
  start_seconds, end_seconds, text, selected confidence metrics and optional
  word timestamps as JSON.
- ProcessingJob optionally points to a Transcription and additionally records
  heartbeat_at, attempt_count and max_attempts.

The unique (media_id, configuration_fingerprint) constraint is the durable
idempotency boundary. The fingerprint currently covers provider, model,
requested language, beam size, VAD, word timestamps and compute type. The same
media may therefore retain multiple transcriptions when those semantics differ.

Segment offsets are numeric seconds. Future footage retrieval can use
media_id + start_seconds + end_seconds without learning storage layout.

Session status is intentionally coarse: pending covers ingestion and basic
indexing, ready means source and technical metadata are available, and
failed means that initial path failed. Transcription uses its own
pending/processing/completed/failed status and cannot demote a ready session.
## Visual index and derived assets

- VisualIndex represents one detector/configuration execution over a MediaAsset
  and owns lifecycle, canonical configuration fingerprint and tool metadata.
- SceneSegment records ordered numeric offsets, duration, optional detector
  score and metadata for one VisualIndex.
- DerivedAsset represents a managed external file, including ownership, kind,
  storage_uri, MIME, checksum, size, dimensions and optional source offsets.
- ProcessingJob may point explicitly to a VisualIndex. This mirrors the
  transcription target without a speculative generic job target.

The unique (media_id, configuration_fingerprint) constraint is the durable
VisualIndex idempotency boundary. Configuration covers detection settings,
periodic interval, frame encoding and temporal deduplication tolerance.

Scene and frame collections are paginated and absent from SessionOut. Images
remain files outside PostgreSQL. representative_frame metadata records
periodic/scene origins and related scene sequence numbers. VisualIndex owns its
pending/processing/completed/failed state and cannot demote a ready MediaSession.

## Temporal index and searchable segments

- TemporalIndex represents one deterministic temporal build over an explicit
  completed Transcription and VisualIndex. It records lifecycle, provenance,
  embedding provider/model/dimensions and the canonical configuration
  fingerprint.
- TemporalSegment is the ordered retrieval unit. It records a positive temporal
  range, deterministic ASR text/search_text, metadata, embedding lifecycle and
  an optional vector(1024).
- TemporalSegmentTranscriptLink, TemporalSegmentSceneLink and
  TemporalSegmentFrameLink are normalized many-to-many evidence associations.
  They answer which original transcript segments, scenes and representative
  frames contributed to a retrieval unit without treating JSON as relational
  identity.
- ProcessingJob may point explicitly to TemporalIndex. This remains clearer for
  the current small set of concrete targets than a speculative generic target.

TemporalIndex identity includes media, both prerequisite IDs, segmentation
durations, interval/text ownership rules, embedding provider/model/dimensions
and similarity metric. Equivalent requests reuse the aggregate and job;
materially different configurations coexist.

The timeline is fully covered even when no speech exists. A silent
TemporalSegment has text/search_text empty, embedding NULL and
embedding_status=not_applicable. It is valid evidence for future temporal or
multimodal expansion, but does not participate in textual vector similarity.

Text embeddings live directly on TemporalSegment because their immutable
provider/model/dimension provenance belongs to TemporalIndex. They are derived
and replaceable: retry deletes and recreates segments, associations and vectors.
The API never returns vector arrays. Primitive search is scoped to one completed
TemporalIndex and returns exact cosine similarity with higher scores better.

## Candidate retrieval

- RetrievalRun is one persisted query or note-batch retrieval over a completed
  TemporalIndex. It records subject/configuration identity, explicit
  semantic_temporal_v1 strategy and pending/processing/completed/failed state.
- RetrievalCandidate is a ranked, positive-duration candidate window for one
  query subject or optional Note. It stores the traceable composite score,
  components and context-expansion metadata.
- RetrievalCandidateTemporalSegment links a candidate to each semantic hit and
  stores that hit's exact cosine score and retrieval rank.

The run fingerprint includes TemporalIndex, normalized arbitrary query or
ordered Note IDs, all retrieval parameters, strategy version and scoring
formula. Equivalent operations reuse one run; different parameters coexist.
Candidate ranks are unique per run subject.

Candidates are hypotheses, not matches. Their [start,end) windows may include
silent TemporalSegments, transcripts, scenes and representative frames reached
through temporal overlap. Only contributing vector hits are normalized as
candidate provenance. The evidence service exposes both sets and preserves
Note.raw unchanged; frames are references by storage_uri, never database blobs
or base64.
