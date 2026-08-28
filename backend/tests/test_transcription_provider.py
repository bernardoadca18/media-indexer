from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import Settings
from app.services.transcription_provider import (
    FasterWhisperProvider,
    TranscriptionConfig,
)


async def test_faster_whisper_provider_normalizes_blank_language(tmp_path: Path) -> None:
    assert Settings(_env_file=None, whisper_language="  ").whisper_language is None

    model = MagicMock()
    model.transcribe.return_value = ([], SimpleNamespace(language=""))
    provider = FasterWhisperProvider(device="cpu", compute_type="int8")
    provider._model = model
    provider._loaded_model_name = "small"

    stream = await provider.transcribe(
        tmp_path / "audio.wav",
        TranscriptionConfig(
            model="small",
            language="  ",
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
        ),
    )

    assert model.transcribe.call_args.kwargs["language"] is None
    assert stream.language is None
