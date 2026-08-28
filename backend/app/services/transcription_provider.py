import asyncio
import importlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


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

    def __init__(self, device: str = "auto", compute_type: str = "auto"):
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("whisper device must be auto, cpu or cuda")
        self.device = device
        self.compute_type = compute_type
        self._model: Any | None = None
        self._loaded_model_name: str | None = None
        self._load_lock = asyncio.Lock()

    def _create_model(self, model_name: str) -> Any:
        faster_whisper = importlib.import_module("faster_whisper")
        device = self.device
        if device == "auto":
            ctranslate2 = importlib.import_module("ctranslate2")
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        return faster_whisper.WhisperModel(
            model_name, device=device, compute_type=self.compute_type
        )

    async def _get_model(self, model_name: str) -> Any:
        async with self._load_lock:
            if self._model is None or self._loaded_model_name != model_name:
                self._model = await asyncio.to_thread(self._create_model, model_name)
                self._loaded_model_name = model_name
            return self._model

    async def transcribe(self, source: Path, config: TranscriptionConfig) -> TranscriptionStream:
        model = await self._get_model(config.model)
        language = normalize_whisper_language(config.language)

        def start() -> tuple[Iterator[Any], Any]:
            segments, info = model.transcribe(
                str(source),
                language=language,
                beam_size=config.beam_size,
                vad_filter=config.vad_filter,
                word_timestamps=config.word_timestamps,
            )
            return iter(segments), info

        iterator, info = await asyncio.to_thread(start)

        async def results() -> AsyncIterator[SegmentResult]:
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

        metadata = {
            "language_probability": getattr(info, "language_probability", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
        }
        return TranscriptionStream(
            language=normalize_whisper_language(getattr(info, "language", None)) or language,
            duration_seconds=getattr(info, "duration", None),
            metadata=metadata,
            segments=results(),
        )
