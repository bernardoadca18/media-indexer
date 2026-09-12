import base64
import json
import time
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.services.alignment_provider import parse_structured_json
from app.services.model_gateway import (
    ModelGatewayClient,
    ModelGatewayTransportError,
    model_gateway,
)


class ImageSemanticProviderError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class InvalidImageSemanticOutputError(ValueError):
    pass


SemanticTerm = Annotated[str, Field(min_length=1, max_length=300)]


class ImageSemanticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=3000)
    subjects: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    environment: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    activities: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    vehicles: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    objects: list[SemanticTerm] = Field(default_factory=list, max_length=50)
    architecture: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    visual_features: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    time_of_day: str | None = Field(default=None, max_length=100)
    weather: str | None = Field(default=None, max_length=100)
    clothing: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    composition: list[SemanticTerm] = Field(default_factory=list, max_length=20)
    visible_text: list[SemanticTerm] = Field(default_factory=list, max_length=30)
    search_terms: list[SemanticTerm] = Field(default_factory=list, max_length=50)
    uncertainties: list[SemanticTerm] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class ImageSemanticProviderResponse:
    result: ImageSemanticResult
    provider_metadata: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    latency_seconds: float


class ImageSemanticProvider(Protocol):
    name: str

    async def describe(
        self, content: bytes, *, mime_type: str, model: str
    ) -> ImageSemanticProviderResponse: ...


class FakeImageSemanticProvider:
    name = "fake-vision"

    def __init__(
        self,
        result: ImageSemanticResult | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.result = result or ImageSemanticResult(
            summary="An urban street at night.",
            description="A person stands beside a car on a brightly lit urban street.",
            subjects=["person"],
            environment=["urban street"],
            vehicles=["car"],
            visual_features=["neon lighting"],
            search_terms=["nightlife", "traffic"],
        )
        self.fail = fail
        self.call_count = 0

    async def describe(
        self, content: bytes, *, mime_type: str, model: str
    ) -> ImageSemanticProviderResponse:
        self.call_count += 1
        if self.fail:
            raise ImageSemanticProviderError("fake vision provider failure")
        return ImageSemanticProviderResponse(
            result=self.result,
            provider_metadata={"model": model, "provider": self.name},
            usage_metadata=None,
            latency_seconds=0.001,
        )


class NinerouterImageSemanticProvider:
    name = "ninerouter"

    def __init__(
        self,
        timeout_seconds: float,
        gateway: ModelGatewayClient = model_gateway,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.gateway = gateway

    async def describe(
        self, content: bytes, *, mime_type: str, model: str
    ) -> ImageSemanticProviderResponse:
        from app.services.image_semantic_prompt import (
            SYSTEM_INSTRUCTION,
            USER_INSTRUCTION,
            response_format,
        )

        encoded = base64.b64encode(content).decode("ascii")
        started = time.monotonic()
        try:
            response = await self.gateway.post(
                "/v1/chat/completions",
                payload={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": USER_INSTRUCTION},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{encoded}",
                                        "detail": "high",
                                    },
                                },
                            ],
                        },
                    ],
                    "temperature": 0,
                    "stream": False,
                    "response_format": response_format(),
                },
                read_timeout_seconds=self.timeout_seconds,
            )
        except ModelGatewayTransportError as exc:
            raise ImageSemanticProviderError(
                "9Router image description failed after bounded transport retries",
                transient=True,
            ) from exc
        if response.status_code >= 400:
            status = response.status_code
            raise ImageSemanticProviderError(
                f"9Router image description failed with HTTP {status}",
                transient=status in {408, 429} or status >= 500,
            )
        latency = time.monotonic() - started
        try:
            payload: Any = response.json()
            raw_content = payload["choices"][0]["message"]["content"]
            if not isinstance(raw_content, str):
                raise TypeError
            result = ImageSemanticResult.model_validate(parse_structured_json(raw_content))
            usage = payload.get("usage")
            provider_metadata = {
                key: payload[key]
                for key in ("id", "model", "provider", "system_fingerprint")
                if key in payload
            }
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise InvalidImageSemanticOutputError(
                "9Router returned an invalid structured image description"
            ) from exc
        return ImageSemanticProviderResponse(
            result=result,
            provider_metadata=provider_metadata or None,
            usage_metadata=usage if isinstance(usage, dict) else None,
            latency_seconds=latency,
        )
