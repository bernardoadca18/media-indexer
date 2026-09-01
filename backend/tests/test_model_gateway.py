from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.model_gateway import ModelGatewayClient


def gateway(client: AsyncMock, *, attempts: int = 3) -> ModelGatewayClient:
    return ModelGatewayClient(
        base_url="http://9router.test",
        api_key="secret",
        connect_timeout_seconds=2.0,
        max_attempts=attempts,
        client=client,
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_terminal_statuses_are_not_retried(status: int) -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(status)

    response = await gateway(client).post(
        "/v1/embeddings",
        payload={"model": "neovids-embedding", "input": ["probe"]},
        read_timeout_seconds=5.0,
    )

    assert response.status_code == status
    assert client.post.await_count == 1


@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_transient_statuses_are_bounded_and_retried(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [httpx.Response(status), httpx.Response(200)]
    monkeypatch.setattr("app.services.model_gateway.asyncio.sleep", AsyncMock())

    response = await gateway(client).post(
        "/v1/chat/completions",
        payload={"model": "neovids-vision", "messages": []},
        read_timeout_seconds=5.0,
    )

    assert response.status_code == 200
    assert client.post.await_count == 2


async def test_gateway_owns_url_auth_and_requested_combo() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200)

    await gateway(client).post(
        "/v1/chat/completions",
        payload={"model": "neovids-vision", "messages": []},
        read_timeout_seconds=5.0,
    )

    call = client.post.await_args
    assert call.args[0] == "http://9router.test/v1/chat/completions"
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert call.kwargs["json"]["model"] == "neovids-vision"
