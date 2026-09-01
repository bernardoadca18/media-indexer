import math

import httpx
import pytest

from app.services.embedding_provider import (
    EmbeddingDimensionError,
    EmbeddingProviderError,
    NinerouterEmbeddingProvider,
    validate_embeddings,
)
from app.services.model_gateway import ModelGatewayClient


class FakeAsyncClient:
    response: httpx.Response
    request: dict[str, object]

    async def post(self, url, *, headers, json, **kwargs):
        type(self).request = {
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": kwargs["timeout"],
        }
        return type(self).response


def gateway(api_key: str) -> ModelGatewayClient:
    return ModelGatewayClient(
        base_url="http://9router.test",
        api_key=api_key,
        connect_timeout_seconds=3.0,
        max_attempts=1,
        client=FakeAsyncClient(),  # type: ignore[arg-type]
    )


async def test_ninerouter_provider_batches_request_and_orders_response() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/embeddings")
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
    provider = NinerouterEmbeddingProvider(12.5, gateway("secret"))

    vectors = await provider.embed(
        ["protect laboratory", "use Aard"],
        model="embedding-alias",
        expected_dimensions=3,
    )

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert FakeAsyncClient.request["url"] == "http://9router.test/v1/embeddings"
    assert FakeAsyncClient.request["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }
    assert FakeAsyncClient.request["json"] == {
        "model": "embedding-alias",
        "input": ["protect laboratory", "use Aard"],
        "dimensions": 3,
    }
    assert isinstance(FakeAsyncClient.request["timeout"], httpx.Timeout)


async def test_ninerouter_provider_maps_http_and_dimension_errors() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/embeddings")
    provider = NinerouterEmbeddingProvider(5.0, gateway(""))

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
