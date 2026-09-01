import json
from uuid import uuid4

import httpx
import pytest

from app.services.alignment_provider import (
    AlignmentFrameEvidence,
    AlignmentProviderError,
    AlignmentProviderRequest,
    InvalidAlignmentOutputError,
    NinerouterMultimodalAlignmentProvider,
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


def provider_request() -> AlignmentProviderRequest:
    return AlignmentProviderRequest(
        candidate_id=uuid4(),
        note_id=uuid4(),
        observation="Aprendo o Aard",
        candidate_start_seconds=30.0,
        candidate_end_seconds=60.0,
        retrieval_rank=1,
        retrieval_score=0.91,
        pass_name="coarse",
        timestamp_resolution_seconds=2.0,
        transcript=(),
        transcript_truncated=False,
        frames=(
            AlignmentFrameEvidence(
                timestamp_seconds=40.0,
                content=b"jpeg-bytes",
                mime_type="image/jpeg",
                storage_uri="local://frame.jpg",
                origin="alignment",
            ),
        ),
    )


def test_alignment_prompt_names_host_owned_timestamp_fields() -> None:
    from app.services.alignment_prompt import (
        ALIGNMENT_PROMPT_VERSION,
        SYSTEM_INSTRUCTION,
    )

    assert ALIGNMENT_PROMPT_VERSION == "alignment_prompt_v2"
    assert "start_seconds" in SYSTEM_INSTRUCTION
    assert "end_seconds" in SYSTEM_INSTRUCTION
    assert "Never shorten" in SYSTEM_INSTRUCTION


async def test_ninerouter_alignment_provider_sends_structured_multimodal_contract() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/chat/completions")
    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={
            "id": "chat-1",
            "model": "alignment-alias",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "matched": True,
                                "start_seconds": 36.0,
                                "end_seconds": 52.0,
                                "confidence": 0.93,
                                "anchors": [
                                    {
                                        "timestamp_seconds": 42.0,
                                        "description": "Aard tutorial appears",
                                    }
                                ],
                                "reasoning_summary": "Visible evidence supports the interval.",
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        },
    )
    provider = NinerouterMultimodalAlignmentProvider(12.5, gateway("secret"))

    response = await provider.align(provider_request(), model="alignment-alias")

    assert response.result.matched
    assert response.result.start_seconds == 36.0
    assert response.provider_metadata == {
        "id": "chat-1",
        "model": "alignment-alias",
    }
    assert response.usage_metadata == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
    }
    sent = FakeAsyncClient.request
    assert sent["url"] == "http://9router.test/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer secret"
    body = sent["json"]
    assert body["model"] == "alignment-alias"
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["response_format"]["type"] == "json_schema"
    user_content = body["messages"][1]["content"]
    assert any(item["type"] == "image_url" for item in user_content)
    image = next(item for item in user_content if item["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "local://frame.jpg" not in json.dumps(body)


async def test_ninerouter_alignment_accepts_json_only_markdown_fence() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/chat/completions")
    raw = json.dumps(
        {
            "matched": False,
            "start_seconds": None,
            "end_seconds": None,
            "confidence": 0.8,
            "anchors": [],
            "reasoning_summary": "The observation is not visible.",
        }
    )
    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={"choices": [{"message": {"content": f"```json\n{raw}\n```"}}]},
    )
    provider = NinerouterMultimodalAlignmentProvider(5.0, gateway(""))

    response = await provider.align(provider_request(), model="alignment-alias")

    assert response.result.matched is False


async def test_ninerouter_alignment_provider_maps_http_failure_after_bound() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/chat/completions")
    FakeAsyncClient.response = httpx.Response(503, request=request)
    provider = NinerouterMultimodalAlignmentProvider(5.0, gateway(""))

    with pytest.raises(AlignmentProviderError, match="HTTP 503") as raised:
        await provider.align(provider_request(), model="alignment-alias")

    assert raised.value.transient
    assert "Authorization" not in FakeAsyncClient.request["headers"]


async def test_ninerouter_alignment_rejects_invalid_or_out_of_bounds_output() -> None:
    request = httpx.Request("POST", "http://9router.test/v1/chat/completions")
    provider = NinerouterMultimodalAlignmentProvider(5.0, gateway(""))

    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={"choices": [{"message": {"content": "not-json"}}]},
    )
    with pytest.raises(InvalidAlignmentOutputError, match="invalid structured"):
        await provider.align(provider_request(), model="alignment-alias")

    FakeAsyncClient.response = httpx.Response(
        200,
        request=request,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "matched": True,
                                "start_seconds": 20.0,
                                "end_seconds": 40.0,
                                "confidence": 0.8,
                                "anchors": [],
                                "reasoning_summary": None,
                            }
                        )
                    }
                }
            ]
        },
    )
    with pytest.raises(InvalidAlignmentOutputError, match="outside"):
        await provider.align(provider_request(), model="alignment-alias")
