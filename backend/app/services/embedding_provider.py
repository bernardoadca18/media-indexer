import math
from collections.abc import Sequence
from typing import Any, Protocol

import httpx


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


class LiteLLMEmbeddingProvider:
    name = "litellm"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        expected_dimensions: int,
    ) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    headers=headers,
                    json={"model": model, "input": list(texts)},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EmbeddingProviderError(
                f"LiteLLM embeddings request failed with HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingProviderError("LiteLLM embeddings request failed") from exc

        try:
            payload: Any = response.json()
            items = payload["data"]
            ordered = sorted(items, key=lambda item: int(item["index"]))
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProviderError("LiteLLM returned an invalid embeddings response") from exc
        if not all(isinstance(vector, list) for vector in vectors):
            raise EmbeddingProviderError("LiteLLM returned an invalid embedding vector")
        return validate_embeddings(
            vectors,
            expected_count=len(texts),
            expected_dimensions=expected_dimensions,
        )
