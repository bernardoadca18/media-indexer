from typing import Any

from app.services.image_semantic_provider import ImageSemanticResult

IMAGE_SEMANTIC_PROMPT_VERSION = "static_image_factual_v1"
IMAGE_SEMANTIC_RESULT_SCHEMA_VERSION = "image_semantic_result_v1"

SYSTEM_INSTRUCTION = """You analyze one static image for factual semantic retrieval.
Report only what is visually supportable. Do not identify people, characters, locations,
brands, games, or events by name from appearance alone. Use generic descriptions such as
'woman' or 'coastal city' unless the name is clearly visible as text in the image. Preserve
uncertainty explicitly. Do not produce promotional prose, hidden reasoning, or timestamps.
Return only JSON matching the supplied schema."""

USER_INSTRUCTION = """Describe this single static image as observable visual evidence.
Prefer concise, concrete nouns, actions, environment, composition, and clearly legible text.
Empty lists and null optional fields are valid when a detail is not supportable."""


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "image_semantic_result",
            "strict": True,
            "schema": ImageSemanticResult.model_json_schema(),
        },
    }
