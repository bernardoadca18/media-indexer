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

Searchable temporal indexing is implemented as a fourth durable stage:

- full-duration deterministic TemporalSegments, including silent intervals;
- normalized links to transcript, scene and representative-frame evidence;
- batch textual embeddings through the OpenAI-compatible LiteLLM proxy;
- pgvector vector(1024) storage and exact cosine similarity scoped by index;
- independent temporal_index jobs, configuration identity and retry-safe rebuild;
- explicit asynchronous API, pagination and low-level vector search.

Candidate Retrieval for notes and footage is implemented:

- persisted RetrievalRun and ranked RetrievalCandidate aggregates;
- batch query/note embedding through the TemporalIndex provider contract;
- thresholding, temporal grouping, bounded context expansion and clamping;
- traceable score components with a weak Note.order chronological prior;
- normalized contributing-hit links with individual cosine similarities;
- evidence payloads spanning transcript, scenes, frames and silent neighbors;
- synchronous arbitrary-query, single-note and batch-note APIs;
- strategy/configuration fingerprints and idempotent run reuse.

Provider-neutral multimodal temporal alignment is implemented:

- persisted AlignmentRun, AlignmentSubject and CandidateAlignment aggregates;
- explicit RetrievalRun/candidate/Note provenance and versioned strategy/prompt;
- candidate-only bounded dense JPEG sampling with managed DerivedAssets;
- OpenAI-compatible LiteLLM multimodal provider with strict structured output;
- explicit matched, unmatched, failed, not_evaluated and no_candidates semantics;
- optional coarse-to-fine refinement and confidence-based early stop;
- independent multimodal_alignment jobs with heartbeat, bounded attempts and
  deterministic retry cleanup;
- asynchronous creation plus run, paginated-result and candidate-detail APIs;
- deterministic fake-provider coverage and opt-in real gameplay scripts.

Consolidated Semantic Timeline is implemented as a read-only projection:

- explicit completed TemporalIndex and optional compatible AlignmentRun inputs;
- paginated/range-filtered TemporalSegments with [start,end) overlap semantics;
- compact transcript, scene and representative-frame provenance;
- immutable ordered Notes with explicit matched, unmatched, no_candidates,
  not_aligned and not_in_alignment_run states;
- selected matched intervals, confidence, anchors and segment relationships;
- fixed batched query shape with no embeddings, media processing or provider
  invocation;
- no persistence duplication and therefore no schema migration.

## Planned

1. Project/Session Media Search.

Editorial work stays in Ideas Hub; voice, planning, composition and rendering
stay in production services.

There is still no OCR, image captioning, generic multimodal search,
Project/Session Media Search, Ideas Hub/Orchestrator integration or
composition/rendering pipeline in this service.
