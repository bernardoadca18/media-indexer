# Roadmap

## Implemented

Stage 1 foundation, Stage 2 local ingestion/ffprobe, and deterministic Stage 3
note parsing.

Timestamped local ASR is implemented as an independent durable processing stage:

- typed job dispatch, heartbeats, stale recovery and bounded attempts;
- provider-neutral transcription port with lazy local faster-whisper;
- persisted Transcription and paginated TranscriptSegment records;
- optional word timestamps and selected ASR metrics;
- configuration-based idempotency and retry-safe incremental batches;
- explicit asynchronous API trigger and query endpoints.

Deterministic visual indexing is implemented as another durable stage:

- deterministic FFmpeg scene detection with persisted SceneSegments;
- periodic and scene-midpoint representative JPEGs with temporal deduplication;
- VisualIndex configuration fingerprints and independent visual_index jobs;
- managed, checksummed DerivedAssets with retry-safe compensating cleanup;
- explicit asynchronous API and paginated scene/frame queries.

## Planned

1. Searchable temporal segments, then embeddings and pgvector.
2. Candidate retrieval for notes and footage.
3. Provider-neutral multimodal alignment where unmatched is valid.
4. Consolidated Semantic Timeline export and project/session Media Search.

Editorial work stays in Ideas Hub; voice, planning, composition and rendering
stay in production services.

There are still no embeddings, pgvector, semantic search, candidate retrieval,
OCR, image captioning, Gemini alignment, multimodal understanding or
consolidated Semantic Timeline.
