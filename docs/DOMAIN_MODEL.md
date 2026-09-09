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
  range, transcript-derived `text`, strategy-derived `search_text`, metadata, embedding lifecycle and
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
and similarity metric. Enriched strategies also include the deterministic
eligible-timeline-note snapshot identity. Equivalent requests reuse the aggregate and job;
materially different configurations coexist.

The timeline is fully covered even when no speech exists. A segment with no ASR
and no eligible human timeline evidence has empty text/search_text, embedding
NULL and `embedding_status=not_applicable`. An ASR-silent segment with an
overlapping structured human description keeps `text` empty while receiving
derived search_text and an embedding. Metadata exposes honest source types and
overlapping note IDs; it does not claim multimodal confirmation.

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


## Multimodal alignment

- AlignmentRun is one versioned multimodal evaluation over a completed
  RetrievalRun. It owns provider/model, strategy, prompt, configuration identity,
  lifecycle, provider metadata and aggregated usage.
- AlignmentSubject is one exact Note.raw or arbitrary-query observation within a
  run. Its state is matched, unmatched, no_candidates or failed and it optionally
  points to the selected CandidateAlignment.
- CandidateAlignment evaluates one RetrievalCandidate for one subject. It
  preserves retrieval rank, matched/unmatched/failed/not_evaluated state,
  optional bounded interval, confidence, anchors, evidence summary, sampling
  resolution, pass count, provider metadata, usage, latency and error.
- DerivedAsset may point to AlignmentRun and represents a managed alignment_frame
  JPEG with candidate/pass/source-window metadata.
- ProcessingJob may point explicitly to AlignmentRun and executes the independent
  multimodal_alignment stage.

The unique (retrieval_run_id, configuration_fingerprint) constraint is the
alignment idempotency boundary. The fingerprint includes the exact ordered
candidate set and subjects plus provider/model, strategy/prompt/result-schema,
frame sampling/encoding, transcript bound, refinement and early-stop settings.
A changed model or material configuration coexists instead of overwriting
previous evidence.

Alignment is hypothesis validation, not retrieval. A matched result requires
candidate-bounded start_seconds < end_seconds and a confidence in [0,1].
unmatched is a successful result with null timestamps and confidence; provider
failure is a distinct failed state. not_evaluated records early stop explicitly.
A run can therefore complete without any match. No MediaSession status transition
depends on AlignmentRun outcome.

Anchors are a small structured JSON payload because they are bounded explanatory
observations, not relational identity. The durable source hypothesis remains the
RetrievalCandidate FK, and exact Note provenance remains the AlignmentSubject
note_id plus preserved observation. Provider reasoning is limited to a concise
evidence summary; hidden chain-of-thought is neither requested nor stored.

Dense alignment frames remain external files. DerivedAsset retains their logical
URI, checksum, size, dimensions, timestamp and source window. Base64 is transport
only. Retry replaces subjects, candidate results and managed frame assets for
the same AlignmentRun while preserving RetrievalRun, RetrievalCandidates and
Note.raw.


## Consolidated Semantic Timeline

The Consolidated Semantic Timeline is a projection, not a persisted entity. Its
identity is the explicit completed TemporalIndex plus an optional compatible
completed AlignmentRun and request range. Existing normalized records remain
the sources of truth, so no duplicate timeline table or migration is required.

A timeline item projects one TemporalSegment with its sequence, [start,end)
interval, deterministic transcript-derived text, silent flag and compact
transcript/scene/frame provenance. It relates selected matched Notes by IDs.
Representative frames remain DerivedAssets and are exposed only by storage_uri
and metadata. Vectors, search_text internals and binary image content are not
part of the contract.

Every session Note is projected in immutable Note.order with exact Note.raw.
Without an AlignmentRun its status is not_aligned. With a run, status comes from
the corresponding AlignmentSubject, including unmatched and no_candidates.
matched additionally projects the selected CandidateAlignment interval,
confidence, bounded anchors and the TemporalSegments overlapping that interval.
The projection never upgrades a candidate hypothesis or unmatched result into a
different domain claim.


## Media Search

Media Search is a synchronous service, not a persisted entity. It exposes public
search APIs without exposing TemporalIndex IDs, RetrievalRun, embeddings or
pgvector. Session search resolves one completed TemporalIndex; project search
aggregates compatible indexes across multiple sessions.

The TemporalIndex resolution policy is explicit and documented. Requests may
include temporal_index_id for explicit selection. Without it, default_latest
selects the most recent completed index. When multiple completed indexes exist
without explicit selection or default_latest semantics, the API returns 409
Conflict rather than silently choosing.

Compatibility signature is SHA-256(embedding_provider + "|" + embedding_model +
"|" + dimensions). Project search groups sessions by this signature and embeds
the query once per group. All sessions in a compatibility group reuse the same
precomputed vector, optimizing embedding cost across sessions.

MediaSearchRequest captures the query text, max_results, min_score, and optional
filters (session_ids, time_range, duration_range). MediaSearchResponse returns
ranked MediaSearchResult items with session_id, session_label, media_id,
[start,end) interval, score, rank and transcript preview. Project responses
include session summaries indicating searched/skipped sessions.

The service reuses CandidateRetriever search logic via the same
TemporalSearchService cosine search. Results are deduplicated within sessions
by configurable overlap threshold and globally ranked by score. No multimodal
alignment is triggered; the API returns deterministic retrieval results only.
