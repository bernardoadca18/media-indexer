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

## Planned

1. Scene detection, representative frames and managed derived assets.
2. Searchable temporal segments, then embeddings and pgvector.
3. Candidate retrieval for notes and footage.
4. Provider-neutral multimodal alignment where unmatched is valid.
5. Consolidated Semantic Timeline export and project/session Media Search.

Editorial work stays in Ideas Hub; voice, planning, composition and rendering
stay in production services.

There is still no scene detection, representative-frame extraction, embeddings,
pgvector, semantic search, candidate retrieval, Gemini alignment or consolidated
Semantic Timeline.
