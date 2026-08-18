# Architecture

The API registers a project/session (video plus raw notes) and streams the source
through a storage port. A persistent job is queued; one in-process worker invokes
ffprobe outside the request and persists results or errors.

The database row is the durable job source of truth; the queue only wakes the
worker. Pending jobs are re-enqueued on startup. UUIDs and storage URIs prevent
consumers from depending on paths. Full notes and parsed units are immutable,
reinforced by database triggers. SHA-256 supports future deduplication.

Known limits: local storage and one API replica; no quotas, auth, remote storage,
leases or retry policy. pgvector waits until embeddings actually exist.
