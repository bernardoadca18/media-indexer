# Architecture

The API registers a project/session (video plus raw notes) and streams the source
through a storage port. A persistent job is queued; one in-process worker invokes
ffprobe outside the request and persists results or errors.

The database row is the durable job source of truth; the queue only wakes the
worker. Pending jobs are re-enqueued on startup. UUIDs and storage URIs prevent
consumers from depending on paths. Full notes and parsed units are immutable,
reinforced by database triggers. SHA-256 supports future deduplication.

Known limits: local storage and one API replica; no quotas, auth, remote storage,
or distributed leases. Vector search uses PostgreSQL with the pgvector extension.

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
## Deterministic visual index

POST /sessions/{id}/visual-indexes ensures a VisualIndex and queues a
visual_index job. SceneDetector and FrameExtractor ports isolate the processor
from FFmpeg and make normal tests deterministic.

The FFmpeg scdet filter emits cut timestamps and scores. The adapter converts
them into contiguous SceneSegments starting at zero and ending at the probed
media duration. Scene detection can be disabled without disabling periodic
sampling. Representative candidates are the midpoint of every scene plus a
configurable periodic grid starting at zero.

Scene candidates are generated first. A periodic candidate within the
configured temporal tolerance is merged into the existing frame and both
origins are preserved in DerivedAsset metadata. This is temporal deduplication,
not perceptual hashing. FFmpeg emits one JPEG scaled to the configured width
with an aspect-ratio-preserving even height.

Storage owns atomic derived writes, checksums, logical URIs and namespace
deletion. The local layout is:

    projects/{project_id}/sessions/{session_id}/derived/visual/
      {visual_index_id}/frames/{deterministic_filename}

VisualIndex identity is the unique media_id plus the SHA-256 of its canonical
detector/configuration JSON. A different configuration creates a new index. At
each attempt, the processor deletes its managed namespace and all output rows
before restarting. On failure it repeats that cleanup and leaves MediaSession
ready. PostgreSQL and the filesystem cannot commit atomically; deterministic
namespaces and compensating deletion are the explicit single-worker MVP limit.

Migration 0003 also replaces the shared raw-note immutability function with
JSON field access. PostgreSQL resolves record fields before the table-name
branch, so the previous NEW.raw/NEW.raw_notes implementation blocked ordinary
MediaSession status updates. Raw note values remain immutable.

## Searchable temporal index and embeddings

POST /sessions/{id}/temporal-indexes explicitly selects one completed
Transcription and one completed VisualIndex. TemporalIndex stores this
provenance plus the canonical segmentation and embedding configuration. Its
identity is media_id plus the full configuration fingerprint; ProcessingJob
keeps an explicit temporal_index_id rather than introducing a generic target
abstraction.

The deterministic builder covers [0, media_duration]. It prefers transcript end
boundaries within min/max duration, then scene ends, then the target duration.
Transcript text is assigned by transcript start so a fallback boundary cannot
duplicate text, while overlap associations still retain all evidence.
TemporalSegmentTranscriptLink, TemporalSegmentSceneLink and
TemporalSegmentFrameLink make provenance queryable without parsing JSON.
Representative-frame membership is [start,end); the last segment explicitly
owns points at the exact media end. Silent segments keep empty text and
embedding_status=not_applicable.

EmbeddingProvider is intentionally embedding-specific. The production adapter
sends arrays of non-empty search_text values to LiteLLM's OpenAI-compatible
/v1/embeddings endpoint. Provider responses are reordered by input index and
validated for count, finite values and exactly 1024 dimensions before
persistence. The configured dimension is also checked against the schema
constant; changing it requires a migration.

Migration 0004 enables pgvector and stores embeddings directly on
TemporalSegment because provider/model/dimension are immutable provenance on
the owning TemporalIndex. Exact cosine search is scoped by temporal_index_id and
returns 1 - cosine distance, so higher scores are better. No approximate index
is created yet: exact search has perfect recall and is adequate for the
expected initial thousands to tens of thousands of segments.

Processing persists segment rows and evidence links first, then embeddings in
configured batches with heartbeat/progress updates. completed is set only after
all applicable vectors are stored. Retry explicitly deletes evidence links and
TemporalSegments, then rebuilds them; it never deletes the source
Transcription or VisualIndex. A temporal failure leaves MediaSession ready.


## Candidate Retrieval

Candidate Retrieval is a synchronous application service, not a ProcessingJob:
it batches query embeddings, delegates exact cosine search to the existing
TemporalSearchService, groups hits and persists the result within the request.
This keeps durable provenance without queueing short work. RetrievalRun owns
kind (query or notes), semantic_temporal_v1 strategy, canonical configuration
and lifecycle. The unique TemporalIndex/fingerprint boundary makes equivalent
runs reusable and a changed subject set or parameter produces a new run.

Hits below the configured threshold are discarded. Remaining unique hits are
sorted by time and greedily grouped when their gap is within the configured
limit and the context-expanded cluster fits the maximum duration. Expansion is
clamped to [0, media_duration]. Oversized groups split before a hit is lost; the
single-cluster fallback centers the maximum window on its strongest hit.

RetrievalCandidate persists rank, [start,end), composite score, score
components and expansion metadata. RetrievalCandidateTemporalSegment is the
normalized provenance edge and retains each contributing similarity plus its
original hit rank. Evidence assembly separately loads all TemporalSegments,
transcript segments, scenes and representative frames overlapping the expanded
window, so silent neighbors are visible without becoming semantic hits.

Semantic relevance is primary. Query weights are mean .60, peak .30 and hit
density .10. Note weights are mean .55, peak .30, density .10 and chronological
prior .05. The prior compares Note.order position with candidate midpoint and
is never a hard ordering constraint. Note.raw is only whitespace-normalized in
memory for embedding and remains immutable in storage.

Migration 0005 adds the retrieval aggregate and provenance edge. Candidate
retrieval requires a completed TemporalIndex and uses that index's configured
EmbeddingProvider/model/dimensions. It does not call a chat or multimodal model,
does not confirm alignment and cannot demote MediaSession.


## Provider-neutral multimodal temporal alignment

POST /retrieval-runs/{id}/alignments creates or reuses an AlignmentRun and
queues a multimodal_alignment job. The retrieval run must be completed and the
optional candidate subset is ownership-validated. ProcessingJob keeps the
explicit alignment_run_id target; the small concrete target set still does not
justify a generic job-target refactor.

AlignmentRun records RetrievalRun provenance, provider/model, strategy and
prompt versions, canonical configuration, lifecycle, provider metadata and
aggregated usage. AlignmentSubject is the exact immutable Note.raw or arbitrary
query evaluation unit. CandidateAlignment records one candidate hypothesis,
retrieval rank, state, optional interval/confidence, bounded anchors, concise
evidence summary, provider metadata, usage, latency, pass count and measured
sampling resolution. The subject selects the best matched result
deterministically by confidence, then retrieval rank and retrieval score.

MultimodalAlignmentProvider is deliberately separate from embeddings and generic
chat. LiteLLMMultimodalAlignmentProvider targets the existing OpenAI-compatible
/v1/chat/completions gateway and knows no underlying Gemini/cloud/local
implementation. It sends temperature zero plus a strict JSON Schema. The shared
prompt explicitly treats each candidate as a hypothesis, permits unmatched, and
forbids fabricated timestamps or hidden chain-of-thought. Returned timestamps
and anchors are rejected if they escape the candidate window.

The worker resolves the source storage_uri locally, samples compact JPEGs only
inside each candidate and labels every frame with an absolute timestamp. It
uniformly widens the spacing when the configured maximum frame count would be
exceeded. Existing representative frames may supplement dense samples without
near-duplicate timestamps. Optional refinement resamples only a padded coarse
match at a finer configured interval; the refinement response is authoritative.
A high-confidence match can stop later ranks, which remain not_evaluated.

Dense evidence JPEGs are managed DerivedAssets under:

    projects/{project_id}/sessions/{session_id}/derived/alignment/
      {alignment_run_id}/frames/{deterministic_filename}

Their checksums, dimensions, source window, candidate ID, pass and sampling
resolution are persisted. Binary bytes and base64 are never stored in database
JSON or returned by APIs; data URLs exist only in the outbound LiteLLM request.

matched and unmatched are both valid provider outcomes. unmatched requires null
timestamps. no_candidates is a valid subject outcome. Provider, timeout,
transport, structured-output and bounds errors produce failed, never unmatched.
AlignmentRun may complete with unmatched/no_candidates subjects and cannot
change a ready MediaSession.

The provider has no internal retry. ProcessingJob bounded attempts are the only
retry layer, preventing multiplicative retry storms. A retry deletes the run's
subjects, candidate results, DerivedAsset rows and alignment storage namespace,
then rebuilds them. This compensating database/filesystem cleanup retains the
same single-worker and non-transactional filesystem limitations as visual
indexing. Fingerprint identity includes retrieval run, ordered candidate subset,
subjects, provider/model, strategy/prompt, result schema and all material frame,
refinement and early-stop settings.
