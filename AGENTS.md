# Media Indexer agent guide

Media Indexer owns audiovisual identity, ingestion, metadata, immutable raw
notes, jobs and later semantic timelines. It does not write scripts, make
editorial decisions, generate voice, compose or render video.

Code is under backend/app; migrations under backend/alembic; tests under
backend/tests; decisions under docs. From backend run alembic upgrade head,
pytest, ruff check app tests and mypy app. From root run docker compose up --build.

Preserve source notes exactly and only add derived fields. Expose logical IDs
and URIs, not host paths. Keep heavy work out of HTTP and persist job state.
Store large files outside PostgreSQL and add migrations for schema changes.
Do not add editorial logic, LLMs, transcription, rendering or broker
infrastructure before a concrete roadmap phase requires them.

Timestamped local transcription is now an active implemented roadmap phase.
Keep it as an independent ProcessingJob and Transcription aggregate; do not fold
it into InitialIndexProcessor. Transcription failures must not invalidate a
ready MediaSession. Tests use fake providers and must never download a model.

Deterministic visual indexing is now an active implemented roadmap phase. Keep
VisualIndex as an independent ProcessingJob aggregate. SceneSegment stores
temporal structure and DerivedAsset owns managed representative-frame files.
Tests use fake scene/frame tools; only smoke tests may require real FFmpeg.

Searchable temporal indexing is now an implemented roadmap phase. Keep
TemporalIndex as an independent job over explicit completed Transcription and
VisualIndex inputs. TemporalSegments must cover silence without invented text,
retain normalized evidence links, and embed only non-empty search_text through
the direct Ollama EmbeddingProvider. Tests use fake providers; real Ollama and
pgvector checks remain explicit smokes.

Candidate Retrieval is now implemented as a synchronous persisted stage over a
completed TemporalIndex. Keep RetrievalRun strategy/configuration identity,
traceable RetrievalCandidate scoring and normalized contributing-hit links.
Note.raw remains immutable; chronological order is only a weak prior. Tests use
fake embeddings and retrieval never claims a confirmed match.

Provider-neutral multimodal temporal alignment is now implemented as an
independent durable stage. Keep AlignmentRun/AlignmentSubject/CandidateAlignment
provenance, explicit matched/unmatched/failed/not_evaluated semantics, bounded
candidate-only frame sampling and versioned prompts. Unmatched/no_candidates are
valid completed outcomes. Tests use fake providers and frame extractors; real
9Router/FFmpeg evaluation is an explicit smoke requiring credentials.

9Router is the provider-neutral OpenAI-compatible gateway for multimodal
alignment. Direct Ollama is the sole production boundary for text embeddings.
Never hardwire an underlying chat/vision provider, persist base64, expose host
paths, request hidden chain-of-thought, or convert provider failures into
unmatched. The Ollama adapter may retry only bounded transient transport
failures; durable alignment retries remain owned by ProcessingJob attempts.

Consolidated Semantic Timeline is now an implemented read-only projection over
an explicit completed TemporalIndex and an optional compatible completed
AlignmentRun. Keep timeline assembly synchronous and side-effect free: never
invoke providers, embeddings, FFmpeg or jobs, never expose vectors/image bytes,
and preserve Note.raw exactly. Use compact normalized evidence references and
the documented [start,end) overlap semantics.

Project/Session Media Search is now implemented as a synchronous public API.
Keep TemporalIndex resolution explicit with documented conflict handling. Embed
the query once per compatibility group, not per session. Reuse CandidateRetriever
logic without duplicating code. No automatic multimodal alignment. Tests use fake
embeddings; real Ollama evaluation is an explicit smoke.

Stage 16 source materialization is supported through read-only MediaAsset metadata/content routes.
Keep lookup restricted to logical IDs and managed storage resolution. Metadata must not expose
storage_uri or host paths; content streaming must never accept a caller-supplied path or URL.

First-class static image memory is now implemented. MediaAsset is the immutable source binary
identity and media_kind distinguishes image from video. Keep operator-declared
MediaAssetProvenance separate from model observation. ImageSemanticIndex is a non-temporal,
versioned durable derivation. The vision strategy uses the provider-neutral 9Router
neovids-vision route, then direct Ollama text embeddings. The operator-semantic strategy persists
an existing checksum-identified human-assisted sidecar and calls only direct Ollama embeddings;
it must never claim or invoke vision. Never create a MediaSession, SceneSegment, DerivedAsset or
fake time range for a source image. Image Search is project-scoped and returns MediaAsset
IDs/content URLs without vectors, storage URIs, host paths or temporal coordinates. Tests use fake
providers; real image analysis is an explicit one-image smoke only.

Hybrid Visual Sources integration with Ideas Hub and the Orchestrator is now active. Preserve the
Image Search/materialization contracts and provenance indexes without adding editorial ranking,
script generation, composition, or rendering. Video Composer Professionalization is the next
cross-system phase and does not expand Media Indexer ownership.
