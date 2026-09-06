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

### Importing notes into a video-only session

A session created without notes (video-only ingest) can receive notes later:

    curl -X POST "http://localhost:8012/projects/$PROJECT_ID/sessions/$SESSION_ID/notes" \
      -F 'notes=@001.txt'

The endpoint rejects empty files (HTTP 400) and conflicts if the session already
has notes (HTTP 409). Notes are split into immutable units preserving raw bytes
exactly. The response is the updated SessionOut with `notes_filename`,
`raw_notes` and the parsed `notes`.

## HDD storage for large sources

When `STORAGE_HDD_ROOT` is configured and an upload exceeds
`STORAGE_HDD_THRESHOLD_BYTES`, the source is written to the secondary HDD
location and referenced by a `local-hdd://` URI instead of the primary
`local://` storage. `STORAGE_HDD_ROOT` is optional; when unset, all sources use
the primary storage regardless of size.

## Desenvolvimento

    cd backend
    pip install -r requirements-dev.txt
    alembic upgrade head
    pytest
    ruff check app tests
    mypy app

### Topologia de testes

A suíte padrão é CPU-only e usa SQLite em memória/fakes. Os testes de API são integralmente async:
`httpx.AsyncClient` + `ASGITransport`, fixtures `pytest-asyncio` e a sessão SQLAlchemy executam no
mesmo event loop. Cada teste recebe engine/storage próprios, portanto a suíte não depende de ordem,
não compartilha estado e não alcança providers externos.

```bash
cd backend
pytest
```

Comportamento que depende de PostgreSQL/pgvector é marcado `postgres` e usa um banco de teste
explicitamente preparado, nunca o banco do operador:

```bash
POSTGRES_TEST_URL=postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer_test \
  alembic upgrade head
POSTGRES_TEST_URL=postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer_test \
  pytest --postgres -m postgres
```

Testes `external` (modelos, 9Router, FFmpeg/credenciais quando aplicável) exigem `--external` e
permanecem opt-in. Não
converta testes pgvector em SQLite: a separação preserva a semântica real sem tornar toda a suíte
unitária dependente de PostgreSQL.

## Managed source materialization

Stage 16 Video Composer resolves original gameplay through read-only logical-ID contracts:

    GET /media-assets/{media_id}
    GET /media-assets/{media_id}/content

Metadata includes the immutable SHA-256, byte size and technical media fields, but never exposes
`storage_uri`, probe blobs or host paths. The content endpoint resolves only a database-owned
MediaAsset through configured managed storage and supports HTTP byte ranges. Callers cannot supply
paths or arbitrary URLs. No schema migration was required for this projection.

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

Non-empty search_text values are embedded in batches directly through Ollama's
`/api/embed` endpoint. Vectors are stored as pgvector vector(1024);
EMBEDDING_DIMENSIONS is validated against the schema and a dimension change
requires a migration. Search uses exact cosine distance scoped to one
TemporalIndex and returns score = 1 - cosine_distance, so larger is more
similar. No HNSW/IVFFlat index is created at the current expected scale.

    OLLAMA_BASE_URL=http://host.docker.internal:11434
    EMBEDDING_MODEL=mxbai-embed-large:latest
    EMBEDDING_DIMENSIONS=1024
    EMBEDDING_TIMEOUT_SECONDS=60
    EMBEDDING_BATCH_SIZE=32
    TEMPORAL_TARGET_DURATION_SECONDS=30
    TEMPORAL_MIN_DURATION_SECONDS=15
    TEMPORAL_MAX_DURATION_SECONDS=60

Docker Compose uses pgvector/pgvector:pg16, preserving PostgreSQL major 16 and
the existing named data volume. Migration 0004 enables the extension
automatically. The API service joins the external `neovids` Docker network and
reaches host Ollama through the stable `host.docker.internal:host-gateway`
mapping on Linux. Multimodal alignment still resolves `9router` by service DNS.
New indexes persist `embedding_provider=ollama`, the exact model tag and 1024
dimensions. Historical index identities are not rewritten or assumed compatible
merely because they share the same vector size.

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

The provider port is multimodal-alignment-specific. Its 9Router adapter uses the
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


## Media Search

Media Search provides a public API for finding footage without requiring
knowledge of internal pipeline components like TemporalIndex IDs, RetrievalRun,
embeddings, or pgvector.

### Session Search

Search within a single session:

    POST /media-search/sessions/{session_id}
    {
        "query": "Geralt learning Aard",
        "max_results": 5
    }

Response:

    {
        "query": "Geralt learning Aard",
        "scope": "session",
        "project_id": "...",
        "session_id": "...",
        "results": [
            {
                "session_id": "...",
                "media_id": "...",
                "start_seconds": 123.4,
                "end_seconds": 156.2,
                "duration_seconds": 32.8,
                "score": 0.89,
                "rank": 1,
                "session_label": "Session 001",
                "media_filename": "gameplay_001.mp4",
                "temporal_index_id": "...",
                "transcript_preview": "...",
                "matched_note_ids": []
            }
        ],
        "searched_sessions": 1,
        "skipped_sessions": 0,
        "strategy": "media_search_v1"
    }

### Project Search

Search across all eligible sessions in a project:

    POST /media-search/projects/{project_id}
    {
        "query": "combat against drowners",
        "max_results": 10,
        "min_score": 0.3
    }

Results are globally ranked by score and deduplicated within sessions.

### Filters

Both endpoints support filters:

- `start_seconds` / `end_seconds`: Time range filter (session search only)
- `session_ids`: Limit search to specific sessions (project search only)
- `exclude_session_ids`: Exclude specific sessions (project search only)
- `min_score`: Minimum relevance score (default 0.25)
- `max_results`: Maximum results to return (default 10, max 100)
- `max_duration_seconds`: Filter by maximum footage duration
- `temporal_index_id`: Explicit index selection (advanced)

### TemporalIndex Resolution

Media Search automatically resolves the appropriate completed TemporalIndex:

1. If `temporal_index_id` is specified, validate and use it
2. Otherwise, select the most recently created completed index matching the
   configured embedding provider
3. If no suitable index exists, return HTTP 409 Conflict

### Query Embedding Reuse

For project search, the query is embedded ONCE per compatibility group
(same provider/model/dimensions), not once per session. This optimizes
cost and latency for multi-session searches.

### Compatibility Validation

Project search only aggregates results from TemporalIndexes with compatible
embedding configurations. Sessions with incompatible indexes are skipped
with an explanatory reason.

### No Automatic Alignment

Media Search performs deterministic vector similarity search only.
It does NOT invoke multimodal alignment by default. Alignment is optional
enrichment when available through existing AlignmentRun data.


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
    WHISPER_MODEL_LIFETIME=resident
    WHISPER_BEAM_SIZE=5
    WHISPER_VAD_FILTER=true
    WHISPER_WORD_TIMESTAMPS=true

WHISPER_DEVICE accepts auto, cpu or cuda. auto selects CUDA when CTranslate2
reports an available CUDA device, otherwise CPU. The default Docker image is
CPU-ready; CUDA operation also requires a GPU-enabled runtime and compatible
host CUDA/cuDNN libraries. Model files use the Hugging Face cache mounted at
/data/models in Docker.

`resident` is the compatible lifetime default and lazily retains one model, replacing it only if a
request selects a different model. `per_job` drops the provider reference after the lazy segment
stream is completely consumed. Both policies serialize Faster-Whisper use across the entire stream;
database writes remain batched. Provider metadata records lifetime and resolved-device semantics.
A released Python reference is observable but is not proof that an allocator returned RAM/VRAM;
real reclamation must be measured with an explicit external-model smoke before operationally
selecting `per_job`.

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

To exercise the real 9Router alias without hardcoded credentials:

    NINEROUTER_BASE_URL=... NINEROUTER_API_KEY=... \
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
keep normal 9Router authentication; a valid `NINEROUTER_API_KEY` is required
for real evaluation. Tests and the
deterministic smoke use a fake EmbeddingProvider and require no credentials.


## Multimodal alignment smokes

After a real RetrievalRun exists, evaluate one or more candidates:

    python scripts/smoke_multimodal_alignment.py \
      --retrieval-run-id UUID --candidate-id UUID

For a readable full note-run evaluation:

    python scripts/evaluate_note_alignment.py --retrieval-run-id UUID

Both commands use persisted source media, real FFmpeg frame extraction and the
configured 9Router vision combo. They require a valid `NINEROUTER_API_KEY`;
authentication failures are terminal and are not bypassed.
Normal pytest uses FakeAlignmentProvider/FakeFrameExtractor and performs no
network, CUDA, model download or real-video processing.


## Media Search smokes

The deterministic media search smoke creates test data and validates session
and project search functionality:

    python scripts/smoke_media_search.py

This verifies:
- Session search returns ranked results
- Project search aggregates multiple sessions
- Query is embedded only once per compatibility group
- Cross-session ranking works correctly
- Zero results is handled properly

For real evaluation with existing data:

    python scripts/search_media.py \
        --project-id UUID \
        --query "Geralt learning Aard"

Or session-specific:

    python scripts/search_media.py \
        --session-id UUID \
        --query "combat against drowners"

The evaluation script uses the configured direct Ollama model. Deterministic
tests inject a fake provider explicitly; production commands do not fail over locally.
