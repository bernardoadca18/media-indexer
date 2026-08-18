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
the LiteLLM-compatible EmbeddingProvider. Tests use fake providers; real
LiteLLM and pgvector checks remain explicit smokes.

Candidate Retrieval is now implemented as a synchronous persisted stage over a
completed TemporalIndex. Keep RetrievalRun strategy/configuration identity,
traceable RetrievalCandidate scoring and normalized contributing-hit links.
Note.raw remains immutable; chronological order is only a weak prior. Tests use
fake embeddings and retrieval never claims a confirmed match.

The next roadmap phase is provider-neutral multimodal temporal alignment where
unmatched is valid. Gemini/multimodal calls, confirmed alignment and editorial
