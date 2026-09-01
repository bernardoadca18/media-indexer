import math
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.embedding_provider import (
    EmbeddingDimensionError,
    EmbeddingProviderError,
    EmbeddingTransportError,
    OllamaEmbeddingProvider,
    validate_embeddings,
)


class FakeAsyncClient:
    response: httpx.Response
    request: dict[str, object]

    async def post(self, url, *, json, **kwargs):
        type(self).request = {
            "url": url,
            "json": json,
            "timeout": kwargs["timeout"],
        }
        return type(self).response


def provider(*, max_attempts: int = 1) -> OllamaEmbeddingProvider:
    return OllamaEmbeddingProvider(
        base_url="http://ollama.test:11434",
        timeout_seconds=12.5,
        connect_timeout_seconds=3.0,
        max_attempts=max_attempts,
        client=FakeAsyncClient(),  # type: ignore[arg-type]
    )


async def test_ollama_provider_batches_request_and_preserves_response_order() -> None:
    request = httpx.Request("POST", "http://ollama.test:11434/api/embed")
    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={
            "model": "mxbai-embed-large:latest",
            "embeddings": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        },
    )
    adapter = provider()

    vectors = await adapter.embed(
        ["protect laboratory", "use Aard"],
        model="mxbai-embed-large:latest",
        expected_dimensions=3,
    )

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert FakeAsyncClient.request["url"] == "http://ollama.test:11434/api/embed"
    assert FakeAsyncClient.request["json"] == {
        "model": "mxbai-embed-large:latest",
        "input": ["protect laboratory", "use Aard"],
    }
    assert isinstance(FakeAsyncClient.request["timeout"], httpx.Timeout)


async def test_ollama_provider_maps_http_and_dimension_errors() -> None:
    request = httpx.Request("POST", "http://ollama.test:11434/api/embed")
    adapter = provider()

    FakeAsyncClient.response = httpx.Response(400, request=request)
    with pytest.raises(EmbeddingProviderError, match="HTTP 400"):
        await adapter.embed(["text"], model="mxbai-embed-large:latest", expected_dimensions=3)

    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={"embeddings": [[1.0, 2.0]]},
    )
    with pytest.raises(EmbeddingDimensionError, match="2 dimensions; expected 3"):
        await adapter.embed(["text"], model="mxbai-embed-large:latest", expected_dimensions=3)


async def test_ollama_provider_bounds_transient_retries(monkeypatch) -> None:
    request = httpx.Request("POST", "http://ollama.test:11434/api/embed")
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        httpx.Response(503, request=request),
        httpx.Response(503, request=request),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.embedding_provider.asyncio.sleep", sleep)
    adapter = OllamaEmbeddingProvider(
        base_url="http://ollama.test:11434",
        timeout_seconds=5,
        connect_timeout_seconds=1,
        max_attempts=2,
        client=client,
    )

    with pytest.raises(EmbeddingTransportError):
        await adapter.embed(["text"], model="mxbai-embed-large:latest", expected_dimensions=3)

    assert client.post.await_count == 2
    sleep.assert_awaited_once_with(1)


def test_embedding_validation_rejects_count_and_non_finite_values() -> None:
    with pytest.raises(EmbeddingProviderError, match="1 vectors for 2 inputs"):
        validate_embeddings([[1.0]], expected_count=2, expected_dimensions=1)
    with pytest.raises(EmbeddingProviderError, match="non-finite"):
        validate_embeddings(
            [[math.inf, 0.0]],
            expected_count=1,
            expected_dimensions=2,
        )
