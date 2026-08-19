from typing import Any

from app.services.alignment_provider import AlignmentProviderRequest, AlignmentProviderResult

ALIGNMENT_STRATEGY = "multimodal_alignment_v1"
ALIGNMENT_PROMPT_VERSION = "alignment_prompt_v1"

SYSTEM_INSTRUCTION = """You validate a small candidate window against one source observation.
The candidate is only a retrieval hypothesis and may be wrong. matched=false is normal.
Use only the timestamped transcript and images supplied. Do not invent events.
For subjective observations, locate the experience that plausibly produced the observation
without claiming the opinion is an objective visual fact.
When matched=true, return the smallest defensible interval inside the candidate.
When matched=false, timestamps must be null. Confidence describes confidence in either result.
Return only the requested structured JSON and never chain-of-thought."""


def build_alignment_text(request: AlignmentProviderRequest) -> str:
    source_label = "RAW NOTE" if request.note_id is not None else "QUERY"
    transcript = "\n".join(
        f"[{item.start_seconds:.3f},{item.end_seconds:.3f}) {item.text}"
        for item in request.transcript
    )
    if not transcript:
        transcript = "(no transcript evidence)"
    return (
        f"{source_label}:\n{request.observation}\n\n"
        f"CANDIDATE:\n"
        f"start={request.candidate_start_seconds:.3f}\n"
        f"end={request.candidate_end_seconds:.3f}\n"
        f"retrieval_rank={request.retrieval_rank}\n"
        f"retrieval_score={request.retrieval_score:.6f}\n"
        f"pass={request.pass_name}\n"
        f"timestamp_resolution_seconds={request.timestamp_resolution_seconds:.3f}\n"
        f"transcript_truncated={str(request.transcript_truncated).lower()}\n\n"
        f"TIMESTAMPED TRANSCRIPT:\n{transcript}\n\n"
        "Each following image is preceded by a FRAME timestamp. "
        "All output timestamps and anchors must remain inside the candidate."
    )


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "multimodal_alignment_result",
            "strict": True,
            "schema": AlignmentProviderResult.model_json_schema(),
        },
    }
