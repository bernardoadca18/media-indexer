# Domain model

- MediaProject groups sessions.
- MediaAsset owns logical URI, filename, checksum, size and probe metadata.
- MediaSession binds a project, primary media and the exact note document.
- Note is an ordered immutable unit parsed from that document.
- ProcessingJob persists status, stage, progress and error.

Consumers use media_id plus future temporal coordinates and never interpret
storage_uri. A repeated project-scoped Idempotency-Key returns the existing
session and job.
