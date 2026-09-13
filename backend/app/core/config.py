from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    database_url: str = (
        "postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer"
    )
    storage_root: Path = Path("./storage")
    storage_root_hdd: Path | None = None
    storage_hdd_threshold_bytes: int = 100 * 1024 * 1024  # 100MB default
    # Docker-side indicator that HDD mount is present (set by docker-compose)
    media_indexer_hdd_mount_path: Path | None = None
    # Temp directory for large file operations (should be HDD-backed)
    temp_root: Path | None = None
    ffprobe_binary: str = "ffprobe"
    worker_enabled: bool = True
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:5174"
    job_stale_timeout_seconds: int = 300
    job_heartbeat_interval_seconds: int = 30
    job_max_attempts: int = 3
    transcription_batch_size: int = 25

    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_language: str | None = None
    whisper_model_lifetime: Literal["resident", "per_job"] = "resident"
    ffmpeg_binary: str = "ffmpeg"
    visual_index_batch_size: int = 25
    scene_detection_enabled: bool = True
    scene_detector: str = "ffmpeg_scdet"
    scene_threshold: float = 10.0
    representative_frame_interval_seconds: float = 10.0
    representative_frame_width: int = 640
    representative_frame_format: str = "jpeg"
    representative_frame_jpeg_quality: int = 85
    representative_frame_dedup_tolerance_seconds: float = 0.5

    ninerouter_base_url: str = "http://9router:20128"
    ninerouter_api_key: str = ""
    model_gateway_connect_timeout_seconds: float = 10.0
    model_gateway_max_attempts: int = 3
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_connect_timeout_seconds: float = 5.0
    embedding_model: str = "mxbai-embed-large:latest"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = 60.0
    embedding_max_attempts: int = 3
    embedding_batch_size: int = 32
    image_max_bytes: int = 25 * 1024 * 1024
    image_max_pixels: int = 100_000_000
    image_max_provenance_characters: int = 20_000
    image_vision_model: str = "neovids-vision"
    image_vision_timeout_seconds: float = 120.0
    image_batch_concurrency: int = 1
    temporal_target_duration_seconds: float = 30.0
    temporal_min_duration_seconds: float = 15.0
    temporal_max_duration_seconds: float = 60.0
    candidate_hit_limit: int = 30
    candidate_max_results: int = 5
    candidate_min_semantic_score: float = 0.25
    candidate_max_segment_gap_seconds: float = 5.0
    candidate_context_before_seconds: float = 15.0
    candidate_context_after_seconds: float = 20.0
    candidate_max_duration_seconds: float = 120.0

    alignment_model: str = "neovids-vision"
    alignment_timeout_seconds: float = 120.0
    alignment_frame_interval_seconds: float = 2.0
    alignment_max_frames: int = 60
    alignment_frame_width: int = 640
    alignment_jpeg_quality: int = 82
    alignment_frame_dedup_tolerance_seconds: float = 0.25
    alignment_max_transcript_characters: int = 12000
    alignment_refinement_enabled: bool = False
    alignment_refinement_frame_interval_seconds: float = 0.5
    alignment_refinement_padding_seconds: float = 4.0
    alignment_refinement_max_frames: int = 40
    alignment_early_stop_confidence: float = 0.90

    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    whisper_word_timestamps: bool = True

    @field_validator("whisper_language", mode="before")
    @classmethod
    def normalize_whisper_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("ninerouter_base_url")
    @classmethod
    def validate_ninerouter_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized.casefold().endswith("/v1"):
            raise ValueError("NINEROUTER_BASE_URL must not include the /v1 suffix")
        return normalized

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized.casefold().endswith("/api"):
            raise ValueError("OLLAMA_BASE_URL must not include the /api suffix")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
