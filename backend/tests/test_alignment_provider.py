import json
from uuid import uuid4

import httpx
import pytest

from app.services.alignment_provider import (
    AlignmentFrameEvidence,
    AlignmentProviderError,
    AlignmentProviderRequest,
    InvalidAlignmentOutputError,
    LiteLLMMultimodalAlignmentProvider,
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


async def test_litellm_alignment_provider_sends_structured_multimodal_contract(
    monkeypatch,
) -> None:
    request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
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
    monkeypatch.setattr(
        "app.services.alignment_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )
    provider = LiteLLMMultimodalAlignmentProvider(
        "http://litellm.test/",
        "secret",
        12.5,
    )

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
    assert FakeAsyncClient.timeout == 12.5
    sent = FakeAsyncClient.request
    assert sent["url"] == "http://litellm.test/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer secret"
    body = sent["json"]
    assert body["model"] == "alignment-alias"
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    user_content = body["messages"][1]["content"]
    assert any(item["type"] == "image_url" for item in user_content)
    image = next(item for item in user_content if item["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "local://frame.jpg" not in json.dumps(body)


async def test_litellm_alignment_provider_maps_http_failure_without_retry(
    monkeypatch,
) -> None:
    request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
    FakeAsyncClient.response = httpx.Response(503, request=request)
    monkeypatch.setattr(
        "app.services.alignment_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )
    provider = LiteLLMMultimodalAlignmentProvider("http://litellm.test", "", 5.0)

    with pytest.raises(AlignmentProviderError, match="HTTP 503") as raised:
        await provider.align(provider_request(), model="alignment-alias")

    assert raised.value.transient
    assert "Authorization" not in FakeAsyncClient.request["headers"]


async def test_litellm_alignment_provider_rejects_invalid_or_out_of_bounds_output(
    monkeypatch,
) -> None:
    request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
    monkeypatch.setattr(
        "app.services.alignment_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )
    provider = LiteLLMMultimodalAlignmentProvider("http://litellm.test", "", 5.0)

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
