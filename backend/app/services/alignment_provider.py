import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.model_gateway import (
    ModelGatewayClient,
    ModelGatewayTransportError,
    model_gateway,
)


class AlignmentProviderError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class InvalidAlignmentOutputError(ValueError):
    pass


def parse_structured_json(raw: str) -> dict[str, Any]:
    """Accept a JSON object or one JSON-only Markdown fence from the gateway."""

    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if (
            len(lines) < 3
            or lines[0].casefold() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise ValueError("invalid structured JSON fence")
        candidate = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise TypeError("structured alignment output must be an object")
    return parsed


class AlignmentAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_seconds: float
    description: str = Field(min_length=1, max_length=300)


class AlignmentProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched: bool
    start_seconds: float | None
    end_seconds: float | None
    confidence: float = Field(ge=0.0, le=1.0)
    anchors: list[AlignmentAnchor] = Field(default_factory=list, max_length=5)
    reasoning_summary: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_match_shape(self) -> "AlignmentProviderResult":
        if self.matched:
            if self.start_seconds is None or self.end_seconds is None:
                raise ValueError("matched result requires start and end timestamps")
            if self.end_seconds <= self.start_seconds:
                raise ValueError("matched result requires end greater than start")
        elif self.start_seconds is not None or self.end_seconds is not None:
            raise ValueError("unmatched result requires null timestamps")
        return self


@dataclass(frozen=True)
class AlignmentTranscriptEvidence:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class AlignmentFrameEvidence:
    timestamp_seconds: float
    content: bytes
    mime_type: str
    storage_uri: str
    origin: str


@dataclass(frozen=True)
class AlignmentProviderRequest:
    candidate_id: UUID
    note_id: UUID | None
    observation: str
    candidate_start_seconds: float
    candidate_end_seconds: float
    retrieval_rank: int
    retrieval_score: float
    pass_name: Literal["coarse", "refinement"]
    timestamp_resolution_seconds: float
    transcript: tuple[AlignmentTranscriptEvidence, ...]
    transcript_truncated: bool
    frames: tuple[AlignmentFrameEvidence, ...]


@dataclass(frozen=True)
class AlignmentProviderResponse:
    result: AlignmentProviderResult
    provider_metadata: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    latency_seconds: float


class MultimodalAlignmentProvider(Protocol):
    name: str

    async def align(
        self,
        request: AlignmentProviderRequest,
        *,
        model: str,
    ) -> AlignmentProviderResponse: ...


def validate_result_bounds(
    result: AlignmentProviderResult,
    request: AlignmentProviderRequest,
) -> None:
    start = request.candidate_start_seconds
    end = request.candidate_end_seconds
    if result.matched:
        assert result.start_seconds is not None
        assert result.end_seconds is not None
        if not (start <= result.start_seconds < result.end_seconds <= end):
            raise InvalidAlignmentOutputError(
                "provider alignment timestamps are outside the candidate window"
            )
    for anchor in result.anchors:
        if not start <= anchor.timestamp_seconds <= end:
            raise InvalidAlignmentOutputError(
                "provider anchor timestamp is outside the candidate window"
            )


class NinerouterMultimodalAlignmentProvider:
    name = "ninerouter"

    def __init__(
        self,
        timeout_seconds: float,
        gateway: ModelGatewayClient = model_gateway,
    ):
        self.timeout_seconds = timeout_seconds
        self.gateway = gateway

    async def align(
        self,
        request: AlignmentProviderRequest,
        *,
        model: str,
    ) -> AlignmentProviderResponse:
        from app.services.alignment_prompt import (
            SYSTEM_INSTRUCTION,
            build_alignment_text,
            response_format,
        )

        content: list[dict[str, Any]] = [{"type": "text", "text": build_alignment_text(request)}]
        for frame in request.frames:
            content.append(
                {
                    "type": "text",
                    "text": f"FRAME @ {frame.timestamp_seconds:.3f}",
                }
            )
            encoded = base64.b64encode(frame.content).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{frame.mime_type};base64,{encoded}",
                        "detail": "low",
                    },
                }
            )

        started = time.monotonic()
        try:
            response = await self.gateway.post(
                "/v1/chat/completions",
                payload={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": content},
                    ],
                    "temperature": 0,
                    "stream": False,
                    "response_format": response_format(),
                },
                read_timeout_seconds=self.timeout_seconds,
            )
        except ModelGatewayTransportError as exc:
            raise AlignmentProviderError(
                "9Router alignment request failed after bounded transport retries",
                transient=True,
            ) from exc
        if response.status_code >= 400:
            status = response.status_code
            raise AlignmentProviderError(
                f"9Router alignment request failed with HTTP {status}",
                transient=status in {408, 429} or status >= 500,
            )
        latency = time.monotonic() - started

        try:
            payload: Any = response.json()
            raw_content = payload["choices"][0]["message"]["content"]
            if not isinstance(raw_content, str):
                raise TypeError
            result = AlignmentProviderResult.model_validate(parse_structured_json(raw_content))
            usage = payload.get("usage")
            provider_metadata = {
                key: payload[key]
                for key in ("id", "model", "provider", "system_fingerprint")
                if key in payload
            }
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise InvalidAlignmentOutputError(
                "9Router returned an invalid structured alignment response"
            ) from exc
        validate_result_bounds(result, request)
        return AlignmentProviderResponse(
            result=result,
            provider_metadata=provider_metadata or None,
            usage_metadata=usage if isinstance(usage, dict) else None,
            latency_seconds=latency,
        )
