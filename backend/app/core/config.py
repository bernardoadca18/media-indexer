from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://media_indexer:media_indexer@localhost:5435/media_indexer"
    )
    storage_root: Path = Path("./storage")
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
    whisper_language: str = ""
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

    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    whisper_word_timestamps: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
