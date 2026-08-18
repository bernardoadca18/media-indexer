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
