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
