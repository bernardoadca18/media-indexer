"""Initial domain."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    timestamps = [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]
    op.create_table("media_projects",
        sa.Column("id", uuid, primary_key=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text), *timestamps)
    op.create_table("media_assets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("media_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(200)), sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("duration_seconds", sa.Float),
        sa.Column("format_name", sa.String(200)), sa.Column("video_codec", sa.String(100)),
        sa.Column("audio_codec", sa.String(100)), sa.Column("fps", sa.Float),
        sa.Column("width", sa.Integer), sa.Column("height", sa.Integer),
        sa.Column("probe_metadata", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_media_assets_project_sha256", "media_assets", ["project_id", "sha256"])
    op.create_table("media_sessions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("media_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", uuid, sa.ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("label", sa.String(200), nullable=False), sa.Column("notes_filename", sa.String(500), nullable=False),
        sa.Column("raw_notes", sa.Text, nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(200)),
        *timestamps, sa.UniqueConstraint("project_id", "idempotency_key", name="uq_session_project_idempotency"))
    op.create_table("notes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("session_id", uuid, sa.ForeignKey("media_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order", sa.Integer, nullable=False), sa.Column("raw", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "order", name="uq_note_session_order"))
    op.create_table("processing_jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("media_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", uuid, sa.ForeignKey("media_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", uuid, sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False), sa.Column("progress", sa.Float, nullable=False),
        sa.Column("error", sa.Text), sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)), *timestamps)
    op.create_index("ix_processing_jobs_status_created", "processing_jobs", ["status", "created_at"])
    op.execute("""CREATE FUNCTION immutable_raw() RETURNS trigger AS $$ BEGIN
      IF TG_TABLE_NAME = 'notes' AND NEW.raw IS DISTINCT FROM OLD.raw THEN RAISE EXCEPTION 'raw is immutable'; END IF;
      IF TG_TABLE_NAME = 'media_sessions' AND NEW.raw_notes IS DISTINCT FROM OLD.raw_notes THEN RAISE EXCEPTION 'raw_notes is immutable'; END IF;
      RETURN NEW; END; $$ LANGUAGE plpgsql;""")
    op.execute("CREATE TRIGGER note_raw_immutable BEFORE UPDATE ON notes FOR EACH ROW EXECUTE FUNCTION immutable_raw()")
    op.execute("CREATE TRIGGER document_raw_immutable BEFORE UPDATE ON media_sessions FOR EACH ROW EXECUTE FUNCTION immutable_raw()")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS immutable_raw() CASCADE")
    op.drop_table("processing_jobs")
    op.drop_table("notes")
    op.drop_table("media_sessions")
    op.drop_table("media_assets")
    op.drop_table("media_projects")
