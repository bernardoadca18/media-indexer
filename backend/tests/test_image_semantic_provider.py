import base64
import json

import httpx
import pytest

from app.services.image_semantic_provider import (
    InvalidImageSemanticOutputError,
    NinerouterImageSemanticProvider,
)
from app.services.model_gateway import ModelGatewayClient


def gateway(client: httpx.AsyncClient) -> ModelGatewayClient:
    return ModelGatewayClient(
        base_url="http://gateway",
        api_key="secret",
        connect_timeout_seconds=1,
        max_attempts=1,
        client=client,
    )


async def test_ninerouter_image_provider_sends_one_image_and_validates_schema() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        result = {
            "summary": "A factual summary.",
            "description": "A factual detailed description.",
            "subjects": ["person"],
            "environment": ["street"],
            "activities": [],
            "vehicles": [],
            "objects": [],
            "architecture": [],
            "visual_features": [],
            "time_of_day": None,
            "weather": None,
            "clothing": [],
            "composition": [],
            "visible_text": [],
            "search_terms": ["street"],
            "uncertainties": [],
        }
        return httpx.Response(
            200,
            json={
                "id": "request-id",
                "model": "routed-model",
                "choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"total_tokens": 10},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NinerouterImageSemanticProvider(5, gateway(client))
    response = await provider.describe(b"image bytes", mime_type="image/png", model="neovids-vision")
    assert seen["model"] == "neovids-vision"
    user_content = seen["messages"][1]["content"]
    images = [item for item in user_content if item["type"] == "image_url"]
    assert len(images) == 1
    encoded = images[0]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"image bytes"
    assert seen["response_format"]["type"] == "json_schema"
    assert response.result.subjects == ["person"]
    assert response.provider_metadata == {"id": "request-id", "model": "routed-model"}
    await client.aclose()


@pytest.mark.parametrize(
    "content",
    ["not-json", json.dumps({"summary": "missing required description"})],
)
async def test_ninerouter_image_provider_rejects_malformed_output(content: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NinerouterImageSemanticProvider(5, gateway(client))
    with pytest.raises(InvalidImageSemanticOutputError):
        await provider.describe(b"image", mime_type="image/jpeg", model="neovids-vision")
    await client.aclose()
