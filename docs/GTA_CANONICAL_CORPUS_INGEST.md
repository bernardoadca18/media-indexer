# GTA canonical corpus operator

`scripts/gta_corpus_operator.py` is the deterministic, resumable adapter for the operator-managed
GTA IV/V/VI corpus. It never creates a MediaProject: every mutating run requires the existing
project UUID supplied with `--project-id`.

The preflight is read-only. It scans only `gta_iv`, `gta_v`, and `gta_vi`, requires the canonical
20 video/timeline pairs and 133 image/description pairs, runs FFprobe/Pillow validation, computes
SHA-256, parses explicit ranges, and reports overlaps and suspicious coverage. Non-landscape
artwork variants are counted as intentionally excluded rather than ingested.

Range validation uses the same 1 ms end-of-media tolerance as the canonical structured timeline
importer. This covers numeric serialization noise without accepting a semantically out-of-bounds
observation.

Timeline sidecars are transformed by `operator_timeline_adapter_v1` and sent through the existing
`timeline_notes_v1` structured import endpoint with provenance
`operator_semantic_timeline_import`. No timestamp is inferred. A preflight error blocks all API
mutation.

Static images are first sent through canonical image ingestion with semantic enqueue disabled.
Their companion Markdown is then sent to the additive operator-semantic endpoint. Strategy
`operator_semantic_text_embedding_v1` persists the exact sidecar text and checksum, constructs
versioned canonical search text, and invokes only the configured direct Ollama embedding provider
(`mxbai-embed-large:latest`, 1024 dimensions). The legacy database fields named
`vision_provider`/`vision_model` contain `operator_import`/`semantic_sidecar` for this strategy;
configuration and provider metadata explicitly record `human_assisted_import` and
`vision_invoked=false`. The 9Router image-semantic provider is not resolved or called.

## Preflight and smoke

Run from `backend`:

```bash
export GTA_PROJECT_ID='<existing-project-uuid>'
export GTA_CORPUS_ROOT='/media/bernardo/HDD500GB1/gameFootage'

PYTHONPATH=. .venv/bin/python scripts/gta_corpus_operator.py \
  --project-id "$GTA_PROJECT_ID" \
  --corpus-root "$GTA_CORPUS_ROOT" \
  --dry-run \
  --json-report /tmp/gta-corpus-preflight.json

PYTHONPATH=. .venv/bin/python scripts/gta_corpus_operator.py \
  --project-id "$GTA_PROJECT_ID" \
  --corpus-root "$GTA_CORPUS_ROOT" \
  --smoke initial \
  --only temporal \
  --wait \
  --concurrency 1

PYTHONPATH=. .venv/bin/python scripts/gta_corpus_operator.py \
  --project-id "$GTA_PROJECT_ID" \
  --corpus-root "$GTA_CORPUS_ROOT" \
  --smoke initial \
  --only static \
  --wait \
  --concurrency 1

PYTHONPATH=. .venv/bin/python scripts/gta_corpus_operator.py \
  --project-id "$GTA_PROJECT_ID" \
  --corpus-root "$GTA_CORPUS_ROOT" \
  --smoke trailer1 \
  --only temporal \
  --wait \
  --concurrency 1
```

`--smoke initial --only temporal` selects GTA IV episode 000. `--smoke initial --only static`
selects the first canonical screenshot, landscape artwork, and Vintage Vice City image.
`--smoke trailer1 --only temporal` selects only GTA VI Trailer 1. Omit `--wait` to enqueue
eligible work and resume on a later invocation.

After an authorized smoke, use the returned logical IDs with the existing read APIs:

```bash
curl -fsS "http://127.0.0.1:8012/projects/$GTA_PROJECT_ID/sessions"
curl -fsS "http://127.0.0.1:8012/sessions/<session-id>"
curl -fsS "http://127.0.0.1:8012/sessions/<session-id>/transcriptions"
curl -fsS "http://127.0.0.1:8012/sessions/<session-id>/visual-indexes"
curl -fsS "http://127.0.0.1:8012/sessions/<session-id>/temporal-indexes"
curl -fsS "http://127.0.0.1:8012/media-assets/<media-id>"
curl -fsS -o /tmp/materialized-image "http://127.0.0.1:8012/media-assets/<media-id>/content"
curl -fsS "http://127.0.0.1:8012/media-assets/<media-id>/provenance"
curl -fsS "http://127.0.0.1:8012/media-assets/<media-id>/image-semantic-indexes"
curl -fsS -X POST "http://127.0.0.1:8012/media-search/projects/$GTA_PROJECT_ID" \
  -H 'Content-Type: application/json' -d '{"query":"GTA street scene","limit":5}'
curl -fsS -X POST "http://127.0.0.1:8012/image-search/projects/$GTA_PROJECT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"query":"Lucia in Vice City","limit":5,"filters":{"game":"gta_vi"}}'
```

## Full resumable batch

Only run after a clean full preflight and explicit operator authorization:

```bash
PYTHONPATH=. .venv/bin/python scripts/gta_corpus_operator.py \
  --project-id "$GTA_PROJECT_ID" \
  --corpus-root "$GTA_CORPUS_ROOT" \
  --only all \
  --wait \
  --concurrency 1 \
  --json-report /tmp/gta-corpus-batch-preflight.json
```

An equivalent completed fingerprint is reused. Pending jobs are not duplicated. Failed
transcription, visual, temporal, or image-embedding derivations use their existing service-level
retry behavior while attempts remain. A failed initial video-ingest job has no public retry
endpoint today and is reported for operator intervention rather than bypassed with direct SQL.

Changing a semantic image sidecar changes its checksum and creates a new immutable semantic
derivation. Historical completed indexes are never rewritten or deleted.
