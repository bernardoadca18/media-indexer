# Media Indexer

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
