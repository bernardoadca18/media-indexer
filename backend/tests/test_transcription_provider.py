from __future__ import annotations

import asyncio
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
    assert stream.close is not None
    await stream.close()


async def test_per_job_model_is_released_after_stream_consumption(
    tmp_path: Path, monkeypatch
) -> None:
    models: list[MagicMock] = []

    def create_model(_: str) -> MagicMock:
        model = MagicMock()
        model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
        models.append(model)
        return model

    provider = FasterWhisperProvider(
        device="cpu",
        compute_type="int8",
        model_lifetime="per_job",
    )
    monkeypatch.setattr(provider, "_create_model", create_model)
    config = TranscriptionConfig(
        model="small",
        language="pt",
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )

    for name in ("first.wav", "second.wav"):
        stream = await provider.transcribe(tmp_path / name, config)
        assert [segment async for segment in stream.segments] == []

    assert len(models) == 2
    assert provider._model is None
    assert provider.model_diagnostics() == {
        "policy": "per_job",
        "configured_device": "cpu",
        "resolved_device": None,
        "compute_type": "int8",
        "resident_model": None,
        "active_transcriptions": 0,
        "model_loads": 2,
        "model_releases": 2,
        "inference_concurrency_limit": 1,
    }


async def test_transcription_streams_are_serialized_until_release(tmp_path: Path) -> None:
    model = MagicMock()
    model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
    provider = FasterWhisperProvider(device="cpu", compute_type="int8")
    provider._model = model
    provider._loaded_model_name = "small"
    config = TranscriptionConfig(
        model="small",
        language="pt",
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )

    first = await provider.transcribe(tmp_path / "first.wav", config)
    second_task = asyncio.create_task(provider.transcribe(tmp_path / "second.wav", config))
    await asyncio.sleep(0)
    assert not second_task.done()
    assert first.close is not None
    await first.close()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert second.close is not None
    await second.close()
