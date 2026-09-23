import asyncio
import json
import math
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.core.config import settings


class EmbeddingProviderError(RuntimeError):
    pass


class EmbeddingTransportError(EmbeddingProviderError):
    pass


class EmbeddingDimensionError(ValueError):
    pass


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

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


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or 500 <= status_code <= 599


class OllamaEmbeddingProvider:
    """Pooled direct Ollama adapter; model fallback is deliberately absent."""

    name = "ollama"

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        connect_timeout_seconds: float,
        max_attempts: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_attempts = max_attempts
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self.connect_timeout_seconds,
                    read=self.timeout_seconds,
                    write=30.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        inputs = list(texts)
        if not inputs:
            return []
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._get_client().post(
                    f"{self.base_url}/api/embed",
                    json={"model": model, "input": inputs},
                    timeout=httpx.Timeout(
                        connect=self.connect_timeout_seconds,
                        read=self.timeout_seconds,
                        write=30.0,
                        pool=10.0,
                    ),
                )
                if response.status_code >= 400:
                    if not is_retryable_status(response.status_code):
                        raise EmbeddingProviderError(
                            f"Ollama embeddings request failed with HTTP {response.status_code}"
                        )
                    response.raise_for_status()
                payload: Any = response.json()
                vectors = payload["embeddings"]
                if not isinstance(vectors, list) or not all(
                    isinstance(vector, list) for vector in vectors
                ):
                    raise EmbeddingProviderError("Ollama returned an invalid embeddings response")
                return validate_embeddings(
                    vectors,
                    expected_count=len(inputs),
                    expected_dimensions=expected_dimensions,
                )
            except (EmbeddingProviderError, EmbeddingDimensionError):
                raise
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise EmbeddingProviderError(
                    "Ollama returned a malformed embeddings response"
                ) from exc
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    break
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise EmbeddingTransportError(
            "Ollama remained unavailable after bounded embedding retries"
        ) from last_error


ollama_embedding_provider = OllamaEmbeddingProvider(
    base_url=settings.ollama_base_url,
    timeout_seconds=settings.embedding_timeout_seconds,
    connect_timeout_seconds=settings.ollama_connect_timeout_seconds,
    max_attempts=settings.embedding_max_attempts,
)
