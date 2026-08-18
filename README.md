# Media Indexer
Also owns durable timestamped ASR and deterministic visual indexing as
independent processing stages.


Camada de entendimento e memória audiovisual do NeoVids. Registra projetos e
sessões, preserva notas brutas, armazena mídias por identidade lógica e extrai
metadata técnica com ffprobe em um job assíncrono.

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
