from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import JobOut, MediaAssetMetadataOut


class ImageProvenanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_owner: str | None = Field(default=None, min_length=1, max_length=200)
    source_class: str | None = Field(default=None, min_length=1, max_length=100)
    asset_class: str | None = Field(default=None, min_length=1, max_length=100)
    collection: str | None = Field(default=None, min_length=1, max_length=200)
    game: str | None = Field(default=None, min_length=1, max_length=200)
    declared_metadata: dict[str, Any] = Field(default_factory=dict)


class ImageProvenanceOut(ImageProvenanceIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    media_id: UUID
    fingerprint: str
    created_at: datetime


class ImageSemanticIndexOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    media_id: UUID
    status: str
    strategy: str
    prompt_version: str
    result_schema_version: str
    canonical_text_version: str
    vision_provider: str
    vision_model: str
    configuration: dict[str, Any]
    configuration_fingerprint: str
    semantic_result: dict[str, Any] | None
    canonical_search_text: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    provider_metadata: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ImageIngestOut(BaseModel):
    media_asset: MediaAssetMetadataOut
    provenance: ImageProvenanceOut
    media_created: bool
    semantic_index: ImageSemanticIndexOut | None = None
    job: JobOut | None = None
    semantic_index_created: bool = False


class ImageSemanticIndexJobOut(BaseModel):
    semantic_index: ImageSemanticIndexOut
    job: JobOut
    created: bool


class OperatorImageSemanticImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["operator_semantic_import_v1"]
    semantic_source: Literal["human_assisted_import"] = "human_assisted_import"
    parser_version: Literal["operator_semantic_markdown_v1"] = "operator_semantic_markdown_v1"
    semantic_text: str = Field(min_length=1, max_length=200_000)
    sidecar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImageSearchFilters(BaseModel):
    source_owner: str | None = Field(default=None, min_length=1, max_length=200)
    source_class: str | None = Field(default=None, min_length=1, max_length=100)
    asset_class: str | None = Field(default=None, min_length=1, max_length=100)
    collection: str | None = Field(default=None, min_length=1, max_length=200)
    game: str | None = Field(default=None, min_length=1, max_length=200)
    exclude_asset_classes: list[str] = Field(default_factory=list, max_length=50)


class ImageSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=20, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    filters: ImageSearchFilters = Field(default_factory=ImageSearchFilters)


class ImageSemanticProvenance(BaseModel):
    index_id: UUID
    strategy: str
    prompt_version: str
    result_schema_version: str
    canonical_text_version: str
    vision_provider: str
    vision_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    configuration_fingerprint: str


class ImageSearchResult(BaseModel):
    source_type: Literal["image"] = "image"
    project_id: UUID
    media_id: UUID
    score: float = Field(ge=-1.0, le=1.0)
    rank: int = Field(ge=1)
    original_filename: str
    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int
    format_name: str
    provenance: ImageProvenanceIn
    semantic_preview: str
    content_url: str
    semantic_index: ImageSemanticProvenance


class ImageSearchResponse(BaseModel):
    query: str
    project_id: UUID
    results: list[ImageSearchResult]
    searched_indexes: int
    compatibility_groups: int
    created_at: datetime
