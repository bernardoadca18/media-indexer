"""Shared 9Router HTTP pool for model-backed Media Indexer stages."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or 500 <= status_code <= 599


class ModelGatewayTransportError(RuntimeError):
    pass


class ModelGatewayClient:
    """One pooled client per process; retries repeat the same gateway request."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        connect_timeout_seconds: float,
        max_attempts: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_attempts = max_attempts
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self.connect_timeout_seconds,
                    read=120.0,
                    write=30.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post(
        self,
        endpoint: str,
        *,
        payload: dict[str, Any],
        read_timeout_seconds: float,
    ) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_error: httpx.RequestError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._get_client().post(
                    f"{self.base_url}{endpoint}",
                    headers=headers,
                    json=payload,
                    timeout=httpx.Timeout(
                        connect=self.connect_timeout_seconds,
                        read=read_timeout_seconds,
                        write=30.0,
                        pool=10.0,
                    ),
                )
                if not is_retryable_status(response.status_code):
                    return response
                if attempt >= self.max_attempts:
                    return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    break
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise ModelGatewayTransportError(
            "9Router remained unavailable after bounded transport retries"
        ) from last_error


model_gateway = ModelGatewayClient(
    base_url=settings.ninerouter_base_url,
    api_key=settings.ninerouter_api_key,
    connect_timeout_seconds=settings.model_gateway_connect_timeout_seconds,
    max_attempts=settings.model_gateway_max_attempts,
)
