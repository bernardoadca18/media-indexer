# Media Indexer

Camada de entendimento e memória audiovisual do NeoVids. Registra projetos e
sessões, preserva notas brutas, armazena mídias por identidade lógica e extrai
metadata técnica, ASR, índice visual e segmentos temporais pesquisáveis em jobs
assíncronos independentes.

## Quick start

    cp .env.example .env
    docker compose up --build

API: http://localhost:8012 (OpenAPI em /docs).

    PROJECT_ID=$(curl -s -X POST http://localhost:8012/projects -H 'Content-Type: application/json' -d '{"name":"The Witcher 1 Playthrough"}' | jq -r .id)
    curl -X POST "http://localhost:8012/projects/$PROJECT_ID/sessions" -F 'label=Session 001' -F 'video=@001.mp4' -F 'notes=@001.txt'

Consulte GET /jobs/{job_id} e GET /sessions/{session_id}.

## Desenvolvimento

    cd backend
    pip install -r requirements-dev.txt
    alembic upgrade head
    pytest
    ruff check app tests
    mypy app

## Timestamped transcription

After the initial_index job makes a session ready, request transcription without
running Whisper in the HTTP request:

    curl -X POST "http://localhost:8012/sessions/$SESSION_ID/transcriptions" \
      -H 'Content-Type: application/json' \
      -d '{"model":"small","word_timestamps":true}'

The response is HTTP 202 and contains both the Transcription and its
ProcessingJob. Query results independently:

    GET /sessions/{session_id}/transcriptions
    GET /transcriptions/{transcription_id}
    GET /transcriptions/{transcription_id}/segments?offset=0&limit=100
    GET /jobs/{job_id}

Equivalent requests reuse the same transcription. Identity is the media plus a
SHA-256 fingerprint of provider, model, requested language, beam size, VAD,
word timestamps and compute type. A different semantic configuration creates a
new transcription.

The worker passes the source media directly to faster-whisper/PyAV. Models are
loaded lazily on first use and the worker reuses the loaded instance. Segment
rows are committed in configurable batches; a retry deletes that
transcription's partial segments before restarting so sequence numbers cannot
be duplicated. A transcription failure does not change a ready MediaSession to
failed.

## Visual indexing

After a session is ready, request a visual index without running FFmpeg in the
HTTP request:

    curl -X POST "http://localhost:8012/sessions/$SESSION_ID/visual-indexes" \
      -H 'Content-Type: application/json' \
      -d '{"periodic_frame_interval_seconds":10,"frame_width":640}'

The HTTP 202 response contains the VisualIndex and its ProcessingJob. Query
metadata without loading image bytes:

    GET /sessions/{session_id}/visual-indexes
    GET /visual-indexes/{visual_index_id}
    GET /visual-indexes/{visual_index_id}/scenes?offset=0&limit=100
    GET /visual-indexes/{visual_index_id}/frames?offset=0&limit=100

FFmpeg scdet creates contiguous SceneSegments. Representative JPEGs combine
periodic timestamps with scene midpoints; scene candidates take precedence and
timestamps closer than the configured tolerance are merged, retaining all
origins in asset metadata. Frames preserve aspect ratio at a compact width.

Derived files are stored under the visual index namespace and exposed only as
storage_uri values. Equivalent media/configuration requests reuse one
VisualIndex and job. Retry removes that index's scene rows, derived rows and
managed files before starting again. Filesystem/database cleanup is
compensating, not a distributed transaction.

Visual settings:

    SCENE_DETECTION_ENABLED=true
    SCENE_DETECTOR=ffmpeg_scdet
    SCENE_THRESHOLD=10.0
    REPRESENTATIVE_FRAME_INTERVAL_SECONDS=10.0
    REPRESENTATIVE_FRAME_WIDTH=640
    REPRESENTATIVE_FRAME_FORMAT=jpeg
    REPRESENTATIVE_FRAME_JPEG_QUALITY=85
    REPRESENTATIVE_FRAME_DEDUP_TOLERANCE_SECONDS=0.5

## Searchable temporal index

A TemporalIndex requires explicit completed transcription_id and visual_index_id:

    curl -X POST "http://localhost:8012/sessions/$SESSION_ID/temporal-indexes" \
      -H 'Content-Type: application/json' \
      -d '{"transcription_id":"...","visual_index_id":"..."}'

The HTTP 202 response contains the TemporalIndex and its independent
ProcessingJob. Query the lightweight aggregate and paginated retrieval units:

    GET /sessions/{session_id}/temporal-indexes
    GET /temporal-indexes/{temporal_index_id}
    GET /temporal-indexes/{temporal_index_id}/segments?offset=0&limit=100
    POST /temporal-indexes/{temporal_index_id}/search

TemporalSegments cover the entire media duration, including silent intervals.
Natural transcript ends are preferred as chunk boundaries, then scene ends,
then the configured target duration. Text is deterministic ASR concatenation;
silent chunks retain empty text and no embedding. Normalized link tables retain
the contributing transcript segments, scenes and representative frames.
Intervals use [start,end), except that a point exactly at media end belongs to
the final segment.

Non-empty search_text values are embedded in batches through the
OpenAI-compatible LiteLLM /v1/embeddings endpoint. The media-indexer only knows
the configured LiteLLM alias. Vectors are stored as pgvector vector(1024);
EMBEDDING_DIMENSIONS is validated against the schema and a dimension change
requires a migration. Search uses exact cosine distance scoped to one
TemporalIndex and returns score = 1 - cosine_distance, so larger is more
similar. No HNSW/IVFFlat index is created at the current expected scale.

    LITELLM_BASE_URL=http://host.docker.internal:4000
    LITELLM_API_KEY=
    EMBEDDING_MODEL=mxbai-large
    EMBEDDING_DIMENSIONS=1024
    EMBEDDING_TIMEOUT_SECONDS=60
    EMBEDDING_BATCH_SIZE=32
    TEMPORAL_TARGET_DURATION_SECONDS=30
    TEMPORAL_MIN_DURATION_SECONDS=15
    TEMPORAL_MAX_DURATION_SECONDS=60

Docker Compose uses pgvector/pgvector:pg16, preserving PostgreSQL major 16 and
the existing named data volume. Migration 0004 enables the extension
automatically. The API service maps host.docker.internal to host-gateway so its
default LiteLLM URL also resolves on Linux Docker; override LITELLM_BASE_URL
when the proxy is on another network.

Equivalent prerequisite/configuration/model requests reuse one TemporalIndex
and job. A retry removes and rebuilds only that index's TemporalSegments,
including evidence links and vectors. Transcription and VisualIndex remain
untouched, and failure cannot demote a ready MediaSession.

## Candidate retrieval

Candidate Retrieval is synchronous because one batched query embedding, exact
vector searches and deterministic grouping are expected to be request-sized
work. Results are still persisted as RetrievalRun and RetrievalCandidate
aggregates for the next multimodal stage.

Technical query retrieval:

    POST /temporal-indexes/{temporal_index_id}/candidates

Note retrieval, for one note or all/selected notes in the index session:

    POST /temporal-indexes/{temporal_index_id}/notes/{note_id}/candidates
    POST /temporal-indexes/{temporal_index_id}/note-retrievals

Inspect persisted provenance:

    GET /retrieval-runs/{retrieval_run_id}
    GET /retrieval-runs/{retrieval_run_id}/candidates
    GET /retrieval-candidates/{candidate_id}/evidence

The semantic_temporal_v1 strategy fetches up to 30 exact-cosine hits by
default, removes hits below the semantic threshold, and merges temporally
adjacent hits while the expanded cluster remains within 120 seconds. Each
cluster receives 15 seconds before and 20 seconds after, clamped to the media
duration. A cluster that would exceed the maximum is split greedily; the
single-hit fallback centers the maximum window on its strongest hit.

Query score is 0.60*semantic_mean + 0.30*semantic_max + 0.10*density. Note
score uses 0.55*mean + 0.30*max + 0.10*density + 0.05*chronological_prior.
The prior compares normalized Note.order with the cluster midpoint and is only
a weak bonus. All components and individual contributing-hit similarities are
persisted. Silent segments can enter the expanded evidence window but are never
given invented embeddings.

Equivalent index, subject set, strategy and configuration reuse the same run.
The evidence endpoint returns the immutable raw Note, contributing hits,
overlapping TemporalSegments/transcript/scenes and frame storage URIs, never
image bytes. A completed TemporalIndex is required; Candidate Retrieval never
changes MediaSession status and does not claim an alignment.

Operational defaults:

    CANDIDATE_HIT_LIMIT=30
    CANDIDATE_MAX_RESULTS=5
    CANDIDATE_MIN_SEMANTIC_SCORE=0.25
    CANDIDATE_MAX_SEGMENT_GAP_SECONDS=5
    CANDIDATE_CONTEXT_BEFORE_SECONDS=15
    CANDIDATE_CONTEXT_AFTER_SECONDS=20
    CANDIDATE_MAX_DURATION_SECONDS=120


## Multimodal temporal alignment

Alignment consumes a completed RetrievalRun and evaluates its candidate windows;
it never sends the full source media to the model:

    POST /retrieval-runs/{retrieval_run_id}/alignments

The HTTP 202 response contains an AlignmentRun and its independent
multimodal_alignment ProcessingJob. A request may include candidate_ids to
evaluate an explicit subset. Query persisted outcomes without image bytes:

    GET /alignment-runs/{alignment_run_id}
    GET /alignment-runs/{alignment_run_id}/results?offset=0&limit=100
    GET /candidate-alignments/{candidate_alignment_id}
    GET /jobs/{job_id}

AlignmentRun owns provider/model, semantic_temporal_v1 input provenance,
multimodal_alignment_v1 strategy, alignment_prompt_v1, canonical configuration
and lifecycle. AlignmentSubject preserves each exact Note.raw or arbitrary query.
CandidateAlignment retains retrieval rank, matched/unmatched/failed/not_evaluated
state, optional interval, confidence, bounded anchors, concise evidence summary,
provider metadata, usage and measured timestamp resolution.

The provider port is multimodal-alignment-specific. Its LiteLLM adapter uses the
OpenAI-compatible /v1/chat/completions endpoint with JSON Schema output, text
evidence and timestamp-labelled JPEGs. Source storage URIs are resolved before
the call; base64 data URLs exist only inside the HTTP request and are never
persisted or exposed by the API.

Each candidate is sampled uniformly at about two seconds, dynamically widened
when needed to respect ALIGNMENT_MAX_FRAMES. Sampling is scoped to the candidate,
uses compact aspect-preserving JPEGs, and may combine existing representative
frames. Optional refinement resamples only a padded coarse match at finer
resolution. When refinement is enabled its valid output is authoritative.
High-confidence matches can stop later ranks; skipped candidates are explicitly
not_evaluated.

Unmatched is a successful domain result with null timestamps. no_candidates is
also successful at subject level. Provider/network/schema errors are failed and
never converted into unmatched. The run can complete with any mix of matched,
unmatched, not_evaluated and no_candidates subjects, and cannot demote a ready
MediaSession.

Equivalent retrieval input, candidate subset, provider/model, prompt/strategy
and frame/refinement configuration reuse one AlignmentRun and job. Retry deletes
that run's CandidateAlignments, AlignmentSubjects and managed alignment-frame
namespace, then deterministically rebuilds it. Provider adapters do not retry;
bounded ProcessingJob attempts are the single retry layer.

Operational defaults:

    ALIGNMENT_MODEL=alignment-flash
    ALIGNMENT_TIMEOUT_SECONDS=120
    ALIGNMENT_FRAME_INTERVAL_SECONDS=2
    ALIGNMENT_MAX_FRAMES=60
    ALIGNMENT_FRAME_WIDTH=640
    ALIGNMENT_JPEG_QUALITY=82
    ALIGNMENT_MAX_TRANSCRIPT_CHARACTERS=12000
    ALIGNMENT_REFINEMENT_ENABLED=false
    ALIGNMENT_REFINEMENT_FRAME_INTERVAL_SECONDS=0.5
    ALIGNMENT_REFINEMENT_PADDING_SECONDS=4
    ALIGNMENT_REFINEMENT_MAX_FRAMES=40
    ALIGNMENT_EARLY_STOP_CONFIDENCE=0.90


## Consolidated Semantic Timeline

The semantic timeline is a synchronous, read-only projection over one explicit
completed TemporalIndex and, optionally, one compatible completed AlignmentRun:

    GET /sessions/{session_id}/semantic-timeline
        ?temporal_index_id={temporal_index_id}
        &alignment_run_id={alignment_run_id}
        &start_seconds=60
        &end_seconds=180
        &offset=0
        &limit=100

The response contains paginated TemporalSegment items, compact transcript,
scene and representative-frame references, and all session Notes in immutable
order. Frame references expose storage_uri and metadata, never bytes or base64.
Embedding/search_text columns are not selected or serialized.

Without alignment provenance, Notes are explicitly not_aligned. With a
compatible completed run, the projection reports matched, unmatched,
no_candidates and not_in_alignment_run without changing persisted state.
Matched Notes include the selected candidate-bounded interval, confidence,
anchors and overlapping TemporalSegment IDs.

Range filtering uses interval overlap with [start,end) semantics:
segment.end > start and segment.start < end. Pagination is applied after the
range filter and total counts the full filtered result. Evidence is batch-loaded
with a fixed query set, avoiding per-item relationship loading. Timeline
assembly performs no provider, embedding, FFmpeg, storage write or job work.
It therefore requires no new entity or migration.


## Long-running jobs

Processing jobs record heartbeat_at, attempt_count and max_attempts. On startup,
pending jobs and processing jobs whose heartbeat is older than
JOB_STALE_TIMEOUT_SECONDS are recovered. A stale job at its attempt limit is
failed permanently. The in-memory queue is only a wake-up mechanism; PostgreSQL
is the durable source of truth.

This design currently assumes one API replica and one in-process worker. It does
not provide distributed leases or locking.

Whisper settings:

    WHISPER_MODEL=small
    WHISPER_DEVICE=auto
    WHISPER_COMPUTE_TYPE=auto
    WHISPER_LANGUAGE=
    WHISPER_BEAM_SIZE=5
    WHISPER_VAD_FILTER=true
    WHISPER_WORD_TIMESTAMPS=true

WHISPER_DEVICE accepts auto, cpu or cuda. auto selects CUDA when CTranslate2
reports an available CUDA device, otherwise CPU. The default Docker image is
CPU-ready; CUDA operation also requires a GPU-enabled runtime and compatible
host CUDA/cuDNN libraries. Model files use the Hugging Face cache mounted at
/data/models in Docker.

## Real visual smoke

Generate a short hard-cut source with FFmpeg (for example, concatenate two-second
red, blue and green color clips), ingest it through the normal session endpoint,
wait for initial_index, then POST a visual index with scene_threshold=8 and a
two-second periodic interval. Poll its job and inspect the scene/frame endpoints.
Verify each local:// URI under STORAGE_ROOT with sha256sum and ffprobe. No media
fixture is committed and this smoke is intentionally outside normal pytest.

## Embedding and pgvector smokes

After the stack is migrated, run from backend:

    python scripts/smoke_pgvector.py
    python scripts/smoke_temporal_index.py

The first verifies the vector extension and exact cosine ordering. The second
builds a small TemporalIndex with a deterministic fake provider, persists
vector(1024) values and searches them through the production repository.

To exercise the real LiteLLM alias without hardcoded credentials:

    LITELLM_BASE_URL=... LITELLM_API_KEY=... \
      python scripts/smoke_embedding.py --text "Geralt learns Aard"


## Candidate Retrieval smokes

The deterministic temporal smoke also performs arbitrary-query and note
Candidate Retrieval, checks ordering/window coverage, and verifies idempotent
run reuse through the production PostgreSQL repositories:

    python scripts/smoke_temporal_index.py

To evaluate one real gameplay note against an existing completed index:

    python scripts/retrieve_note_candidates.py \
      --temporal-index-id UUID --note-id UUID

The command prints ranked HH:MM:SS windows and scores. Real embedding commands
keep normal LiteLLM authentication; the current environment's prior 401 means a
valid LITELLM_API_KEY is required for real evaluation. Tests and the
deterministic smoke use a fake EmbeddingProvider and require no credentials.


## Multimodal alignment smokes

After a real RetrievalRun exists, evaluate one or more candidates:

    python scripts/smoke_multimodal_alignment.py \
      --retrieval-run-id UUID --candidate-id UUID

For a readable full note-run evaluation:

    python scripts/evaluate_note_alignment.py --retrieval-run-id UUID

Both commands use persisted source media, real FFmpeg frame extraction and the
configured LiteLLM chat model alias. They require a valid LITELLM_API_KEY; the
known credential-less environment returns HTTP 401 and is not bypassed.
Normal pytest uses FakeAlignmentProvider/FakeFrameExtractor and performs no
network, CUDA, model download or real-video processing.
