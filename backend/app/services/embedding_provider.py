import math
from collections.abc import Sequence
from typing import Any, Protocol

from app.services.model_gateway import (
    ModelGatewayClient,
    ModelGatewayTransportError,
    model_gateway,
)


class EmbeddingProviderError(RuntimeError):
    pass


class EmbeddingDimensionError(ValueError):
    pass


class EmbeddingProvider(Protocol):
    name: str

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]: ...


def validate_embeddings(
    embeddings: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimensions: int,
) -> list[list[float]]:
    if len(embeddings) != expected_count:
        raise EmbeddingProviderError(
            f"embedding provider returned {len(embeddings)} vectors for {expected_count} inputs"
        )
    validated: list[list[float]] = []
    for vector in embeddings:
        if len(vector) != expected_dimensions:
            raise EmbeddingDimensionError(
                f"embedding has {len(vector)} dimensions; expected {expected_dimensions}"
            )
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingProviderError("embedding contains a non-finite value")
        validated.append(values)
    return validated


class FakeEmbeddingProvider:
    """Deterministic fake embedding provider for testing."""

    def __init__(
        self,
        *,
        name: str = "fake",
        dimensions: int = 1024,
        fail: bool = False,
    ):
        self._name = name
        self.dimensions = dimensions
        self.fail = fail
        self.batches: list[list[str]] = []
        self.embed_call_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        self.batches.append(list(texts))
        self.embed_call_count += 1
        if self.fail:
            raise EmbeddingProviderError("fake provider failure")
        if expected_dimensions != self.dimensions:
            raise EmbeddingDimensionError(
                f"expected {expected_dimensions} dimensions; got {self.dimensions}"
            )
        return [[float(index + 1)] + [0.0] * (self.dimensions - 1) for index, _ in enumerate(texts)]

    def fake_embedding(self, index: int) -> list[float]:
        """Generate a deterministic fake embedding for test setup."""
        return [float(index)] + [0.0] * (self.dimensions - 1)


class NinerouterEmbeddingProvider:
    name = "ninerouter"

    def __init__(
        self,
        timeout_seconds: float,
        gateway: ModelGatewayClient = model_gateway,
    ):
        self.timeout_seconds = timeout_seconds
        self.gateway = gateway

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self.gateway.post(
                "/v1/embeddings",
                payload={
                    "model": model,
                    "input": list(texts),
                    "dimensions": expected_dimensions,
                },
                read_timeout_seconds=self.timeout_seconds,
            )
        except ModelGatewayTransportError as exc:
            raise EmbeddingProviderError(
                "9Router embeddings request failed after bounded transport retries"
            ) from exc
        if response.status_code >= 400:
            raise EmbeddingProviderError(
                f"9Router embeddings request failed with HTTP {response.status_code}"
            )

        try:
            payload: Any = response.json()
            items = payload["data"]
            ordered = sorted(items, key=lambda item: int(item["index"]))
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProviderError("9Router returned an invalid embeddings response") from exc
        if not all(isinstance(vector, list) for vector in vectors):
            raise EmbeddingProviderError("9Router returned an invalid embedding vector")
        return validate_embeddings(
            vectors,
            expected_count=len(texts),
            expected_dimensions=expected_dimensions,
        )
