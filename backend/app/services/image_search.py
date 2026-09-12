import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaProject
from app.repositories.image import ImageSemanticIndexRepository, SearchableImage
from app.schemas.image import (
    ImageProvenanceIn,
    ImageSearchRequest,
    ImageSearchResponse,
    ImageSearchResult,
    ImageSemanticProvenance,
)
from app.services.embedding_provider import EmbeddingProvider

logger = structlog.get_logger(__name__)


class ImageSearchError(ValueError):
    pass


@dataclass(frozen=True)
class ImageEmbeddingCompatibility:
    provider: str
    model: str
    dimensions: int

    @property
    def signature(self) -> str:
        raw = f"{self.provider}\0{self.model}\0{self.dimensions}"
        return hashlib.sha256(raw.encode()).hexdigest()


def _normalize_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ImageSearchError("query cannot be empty")
    return normalized


class ImageSearchService:
    def __init__(self, db: AsyncSession, embedding_provider: EmbeddingProvider) -> None:
        self.db = db
        self.embedding_provider = embedding_provider

    async def search_project(
        self, project_id: UUID, request: ImageSearchRequest
    ) -> ImageSearchResponse:
        if await self.db.get(MediaProject, project_id) is None:
            raise LookupError("project not found")
        query = _normalize_query(request.query)
        filters = request.filters
        repository = ImageSemanticIndexRepository(self.db)
        candidates = await repository.searchable_for_project(
            project_id,
            source_owner=filters.source_owner,
            source_class=filters.source_class,
            asset_class=filters.asset_class,
            collection=filters.collection,
            game=filters.game,
            exclude_asset_classes=filters.exclude_asset_classes,
        )
        groups: dict[str, list[SearchableImage]] = {}
        compatibility: dict[str, ImageEmbeddingCompatibility] = {}
        for item in candidates:
            identity = ImageEmbeddingCompatibility(
                item.index.embedding_provider,
                item.index.embedding_model,
                item.index.embedding_dimensions,
            )
            groups.setdefault(identity.signature, []).append(item)
            compatibility[identity.signature] = identity

        ranked: list[tuple[SearchableImage, float]] = []
        for signature, items in groups.items():
            identity = compatibility[signature]
            if identity.provider != self.embedding_provider.name:
                raise ImageSearchError(
                    f"embedding provider {identity.provider!r} is not available for query"
                )
            vectors = await self.embedding_provider.embed(
                [query],
                model=identity.model,
                expected_dimensions=identity.dimensions,
            )
            by_id = {item.index.id: item for item in items}
            for index_id, score in await repository.rank(items, vectors[0], request.limit):
                if score >= request.min_score:
                    ranked.append((by_id[index_id], score))
        ranked.sort(key=lambda item: (-item[1], str(item[0].index.id)))
        deduplicated: list[tuple[SearchableImage, float]] = []
        seen_media: set[UUID] = set()
        for ranked_item in ranked:
            if ranked_item[0].media.id in seen_media:
                continue
            seen_media.add(ranked_item[0].media.id)
            deduplicated.append(ranked_item)
        ranked = deduplicated[: request.limit]

        results = [self._result(item, score, rank) for rank, (item, score) in enumerate(ranked, 1)]
        logger.info(
            "image_search_completed",
            project_id=str(project_id),
            result_count=len(results),
            searched_indexes=len(candidates),
            compatibility_groups=len(groups),
        )
        return ImageSearchResponse(
            query=query,
            project_id=project_id,
            results=results,
            searched_indexes=len(candidates),
            compatibility_groups=len(groups),
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _result(item: SearchableImage, score: float, rank: int) -> ImageSearchResult:
        index = item.index
        media = item.media
        provenance = item.provenance
        if (
            media.content_type is None
            or media.width is None
            or media.height is None
            or media.format_name is None
            or index.canonical_search_text is None
        ):
            raise ImageSearchError("completed image index has incomplete media metadata")
        semantic = index.semantic_result or {}
        preview = str(semantic.get("summary") or semantic.get("description") or "").strip()
        return ImageSearchResult(
            project_id=media.project_id,
            media_id=media.id,
            score=max(-1.0, min(1.0, score)),
            rank=rank,
            original_filename=media.original_filename,
            content_type=media.content_type,
            byte_size=media.byte_size,
            sha256=media.sha256,
            width=media.width,
            height=media.height,
            format_name=media.format_name,
            provenance=ImageProvenanceIn(
                source_owner=provenance.source_owner,
                source_class=provenance.source_class,
                asset_class=provenance.asset_class,
                collection=provenance.collection,
                game=provenance.game,
                declared_metadata=provenance.declared_metadata,
            ),
            semantic_preview=preview,
            content_url=f"/media-assets/{media.id}/content",
            semantic_index=ImageSemanticProvenance(
                index_id=index.id,
                strategy=index.strategy,
                prompt_version=index.prompt_version,
                result_schema_version=index.result_schema_version,
                canonical_text_version=index.canonical_text_version,
                vision_provider=index.vision_provider,
                vision_model=index.vision_model,
                embedding_provider=index.embedding_provider,
                embedding_model=index.embedding_model,
                embedding_dimensions=index.embedding_dimensions,
                configuration_fingerprint=index.configuration_fingerprint,
            ),
        )
