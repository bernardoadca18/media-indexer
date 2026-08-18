# Architecture

The API registers a project/session (video plus raw notes) and streams the source
through a storage port. A persistent job is queued; one in-process worker invokes
ffprobe outside the request and persists results or errors.

The database row is the durable job source of truth; the queue only wakes the
worker. Pending jobs are re-enqueued on startup. UUIDs and storage URIs prevent
consumers from depending on paths. Full notes and parsed units are immutable,
reinforced by database triggers. SHA-256 supports future deduplication.

Known limits: local storage and one API replica; no quotas, auth, remote storage,
or distributed leases. pgvector waits until embeddings actually exist.

## Job execution and recovery

The runner resolves a typed processor factory from ProcessingJob.job_type.
initial_index remains responsible for ffprobe and session readiness;
transcription is a separate durable job. This preserves independent failure,
retry and observability boundaries for long stages.

The runner updates heartbeat_at from a separate database session while a
processor runs. At startup it re-enqueues pending jobs and resets stale
processing jobs to pending. Staleness is configurable. attempt_count increments
when execution is claimed, max_attempts bounds recovery and explicit retry, and
jobs beyond the limit become failed. PostgreSQL is authoritative; the asyncio
queue only wakes the single worker.

There is deliberately no distributed claim or lease. Running multiple replicas
could execute the same job concurrently, so deployment is limited to one API
replica/worker until distributed coordination is implemented.

## Transcription pipeline

POST /sessions/{id}/transcriptions ensures a Transcription and queues a
transcription job, returning 202. The provider port isolates the processor from
FasterWhisperProvider and lets tests use deterministic fakes. The real provider
loads models only on demand, reuses the active model, and runs blocking model
and iterator work in threads.

The source media URI is resolved and passed directly to faster-whisper. PyAV
performs decoding, avoiding a redundant derived audio file at this stage.
Segments are persisted in configurable batches with numeric second offsets.
Word timestamps, when enabled, are stored as JSON on their segment.

A configuration fingerprint prevents duplicate equivalent transcriptions while
allowing another model or materially different settings. Partial batches may
exist while status is processing or failed. Every retry first deletes all
segments belonging to that same Transcription, then restarts sequence numbering;
only a fully consumed provider stream marks it completed.

MediaSession.status describes ingestion and basic metadata readiness. Derived
transcription state lives on Transcription and its ProcessingJob, so ASR failure
does not make an otherwise usable session failed.
