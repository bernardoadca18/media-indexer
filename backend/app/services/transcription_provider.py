import asyncio
import importlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


def normalize_whisper_language(value: str | None) -> str | None:
    """Normalize blank language values to Faster-Whisper autodetection."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True)
class TranscriptionConfig:
    model: str
    language: str | None
    beam_size: int
    vad_filter: bool
    word_timestamps: bool


@dataclass(frozen=True)
class WordResult:
    word: str
    start: float
    end: float
    probability: float | None


@dataclass(frozen=True)
class SegmentResult:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    words: list[WordResult] | None = None


@dataclass(frozen=True)
class TranscriptionStream:
    language: str | None
    duration_seconds: float | None
    metadata: dict[str, Any]
    segments: AsyncIterator[SegmentResult]
    close: Callable[[], Awaitable[None]] | None = None


class TranscriptionProvider(Protocol):
    name: str

    async def transcribe(
        self, source: Path, config: TranscriptionConfig
    ) -> TranscriptionStream: ...


def _next(iterator: Iterator[Any]) -> tuple[bool, Any | None]:
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


class FasterWhisperProvider:
    name = "faster-whisper"

    def __init__(
        self,
        device: str = "auto",
        compute_type: str = "auto",
        model_lifetime: Literal["resident", "per_job"] = "resident",
    ):
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("whisper device must be auto, cpu or cuda")
        if model_lifetime not in {"resident", "per_job"}:
            raise ValueError("whisper model lifetime must be resident or per_job")
        self.device = device
        self.compute_type = compute_type
        self.model_lifetime = model_lifetime
        self._model: Any | None = None
        self._loaded_model_name: str | None = None
        self._use_lock = asyncio.Lock()
        self._model_loads = 0
        self._model_releases = 0
        self._active_transcriptions = 0
        self._resolved_device: str | None = None

    def _create_model(self, model_name: str) -> Any:
        faster_whisper = importlib.import_module("faster_whisper")
        device = self.device
        if device == "auto":
            ctranslate2 = importlib.import_module("ctranslate2")
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        self._resolved_device = device
        return faster_whisper.WhisperModel(
            model_name, device=device, compute_type=self.compute_type
        )

    async def _load_model(self, model_name: str, *, per_job: bool) -> Any:
        if per_job:
            model = await asyncio.to_thread(self._create_model, model_name)
            self._model_loads += 1
            return model
        if self._model is None or self._loaded_model_name != model_name:
            if self._model is not None:
                self._model = None
                self._loaded_model_name = None
                self._model_releases += 1
            self._model = await asyncio.to_thread(self._create_model, model_name)
            self._loaded_model_name = model_name
            self._model_loads += 1
        return self._model

    async def transcribe(self, source: Path, config: TranscriptionConfig) -> TranscriptionStream:
        await self._use_lock.acquire()
        per_job = self.model_lifetime == "per_job"
        language = normalize_whisper_language(config.language)
        model: Any | None = None
        try:
            model = await self._load_model(config.model, per_job=per_job)

            def start() -> tuple[Iterator[Any], Any]:
                assert model is not None
                segments, info = model.transcribe(
                    str(source),
                    language=language,
                    beam_size=config.beam_size,
                    vad_filter=config.vad_filter,
                    word_timestamps=config.word_timestamps,
                )
                return iter(segments), info

            iterator, info = await asyncio.to_thread(start)
            self._active_transcriptions += 1
        except Exception:
            if per_job and model is not None:
                self._model_releases += 1
            self._use_lock.release()
            raise
        assert model is not None

        released = False

        async def release() -> None:
            nonlocal model, released
            if released:
                return
            released = True
            self._active_transcriptions -= 1
            if per_job:
                model = None
                self._model_releases += 1
            self._use_lock.release()

        async def results() -> AsyncIterator[SegmentResult]:
            try:
                while True:
                    found, segment = await asyncio.to_thread(_next, iterator)
                    if not found:
                        break
                    assert segment is not None
                    words = None
                    if config.word_timestamps and segment.words:
                        words = [
                            WordResult(
                                word=word.word,
                                start=float(word.start),
                                end=float(word.end),
                                probability=getattr(word, "probability", None),
                            )
                            for word in segment.words
                        ]
                    yield SegmentResult(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=segment.text,
                        avg_logprob=getattr(segment, "avg_logprob", None),
                        no_speech_prob=getattr(segment, "no_speech_prob", None),
                        compression_ratio=getattr(segment, "compression_ratio", None),
                        words=words,
                    )
            finally:
                await release()

        metadata = {
            "language_probability": getattr(info, "language_probability", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
            "model_lifetime_policy": self.model_lifetime,
            "resolved_device": self._resolved_device or self.device,
            "inference_concurrency_limit": 1,
        }
        return TranscriptionStream(
            language=normalize_whisper_language(getattr(info, "language", None)) or language,
            duration_seconds=getattr(info, "duration", None),
            metadata=metadata,
            segments=results(),
            close=release,
        )

    def model_diagnostics(self) -> dict[str, object]:
        return {
            "policy": self.model_lifetime,
            "configured_device": self.device,
            "resolved_device": self._resolved_device,
            "compute_type": self.compute_type,
            "resident_model": self._loaded_model_name,
            "active_transcriptions": self._active_transcriptions,
            "model_loads": self._model_loads,
            "model_releases": self._model_releases,
            "inference_concurrency_limit": 1,
        }
