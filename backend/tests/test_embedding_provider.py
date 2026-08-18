import math

import httpx
import pytest

from app.services.embedding_provider import (
    EmbeddingDimensionError,
    EmbeddingProviderError,
    LiteLLMEmbeddingProvider,
    validate_embeddings,
)


class FakeAsyncClient:
    response: httpx.Response
    request: dict[str, object]
    timeout: float

    def __init__(self, *, timeout: float):
        type(self).timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, url, *, headers, json):
        type(self).request = {"url": url, "headers": headers, "json": json}
        return type(self).response


async def test_litellm_provider_batches_openai_compatible_request_and_orders_response(
    monkeypatch,
) -> None:
    request = httpx.Request("POST", "http://litellm.test/v1/embeddings")
    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={
            "data": [
                {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            ]
        },
    )
    monkeypatch.setattr("app.services.embedding_provider.httpx.AsyncClient", FakeAsyncClient)
    provider = LiteLLMEmbeddingProvider("http://litellm.test/", "secret", 12.5)

    vectors = await provider.embed(
        ["protect laboratory", "use Aard"],
        model="embedding-alias",
        expected_dimensions=3,
    )

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert FakeAsyncClient.timeout == 12.5
    assert FakeAsyncClient.request == {
        "url": "http://litellm.test/v1/embeddings",
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer secret",
        },
        "json": {
            "model": "embedding-alias",
            "input": ["protect laboratory", "use Aard"],
        },
    }


async def test_litellm_provider_maps_http_and_dimension_errors(monkeypatch) -> None:
    request = httpx.Request("POST", "http://litellm.test/v1/embeddings")
    monkeypatch.setattr("app.services.embedding_provider.httpx.AsyncClient", FakeAsyncClient)
    provider = LiteLLMEmbeddingProvider("http://litellm.test", "", 5.0)

    FakeAsyncClient.response = httpx.Response(503, request=request)
    with pytest.raises(EmbeddingProviderError, match="HTTP 503"):
        await provider.embed(["text"], model="alias", expected_dimensions=3)
    assert "Authorization" not in FakeAsyncClient.request["headers"]

    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
    )
    with pytest.raises(EmbeddingDimensionError, match="2 dimensions; expected 3"):
        await provider.embed(["text"], model="alias", expected_dimensions=3)


def test_embedding_validation_rejects_count_and_non_finite_values() -> None:
    with pytest.raises(EmbeddingProviderError, match="1 vectors for 2 inputs"):
        validate_embeddings([[1.0]], expected_count=2, expected_dimensions=1)
    with pytest.raises(EmbeddingProviderError, match="non-finite"):
        validate_embeddings(
            [[math.inf, 0.0]],
            expected_count=1,
            expected_dimensions=2,
        )
