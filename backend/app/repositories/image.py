import math
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImageSemanticIndex, MediaAsset, MediaAssetProvenance


@dataclass(frozen=True)
class SearchableImage:
    index: ImageSemanticIndex
    media: MediaAsset
    provenance: MediaAssetProvenance


class ImageSemanticIndexRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, index_id: UUID) -> ImageSemanticIndex | None:
        return await self.db.get(ImageSemanticIndex, index_id)

    async def by_fingerprint(
        self, media_id: UUID, fingerprint: str
    ) -> ImageSemanticIndex | None:
        result = await self.db.execute(
            select(ImageSemanticIndex).where(
                ImageSemanticIndex.media_id == media_id,
                ImageSemanticIndex.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_media(self, media_id: UUID) -> Sequence[ImageSemanticIndex]:
        result = await self.db.execute(
            select(ImageSemanticIndex)
            .where(ImageSemanticIndex.media_id == media_id)
            .order_by(ImageSemanticIndex.created_at.desc())
        )
        return result.scalars().all()

    async def searchable_for_project(
        self,
        project_id: UUID,
        *,
        source_owner: str | None = None,
        source_class: str | None = None,
        asset_class: str | None = None,
        collection: str | None = None,
        game: str | None = None,
        exclude_asset_classes: Sequence[str] = (),
    ) -> list[SearchableImage]:
        statement = (
            select(ImageSemanticIndex, MediaAsset, MediaAssetProvenance)
            .join(MediaAsset, MediaAsset.id == ImageSemanticIndex.media_id)
            .join(MediaAssetProvenance, MediaAssetProvenance.media_id == MediaAsset.id)
            .where(
                ImageSemanticIndex.project_id == project_id,
                ImageSemanticIndex.status == "completed",
                ImageSemanticIndex.embedding.is_not(None),
                MediaAsset.media_kind == "image",
            )
        )
        for column, value in (
            (MediaAssetProvenance.source_owner, source_owner),
            (MediaAssetProvenance.source_class, source_class),
            (MediaAssetProvenance.asset_class, asset_class),
            (MediaAssetProvenance.collection, collection),
            (MediaAssetProvenance.game, game),
        ):
            if value is not None:
                statement = statement.where(column == value)
        if exclude_asset_classes:
            statement = statement.where(
                MediaAssetProvenance.asset_class.not_in(exclude_asset_classes)
                | MediaAssetProvenance.asset_class.is_(None)
            )
        result = await self.db.execute(statement.order_by(ImageSemanticIndex.created_at.desc()))
        return [SearchableImage(row[0], row[1], row[2]) for row in result.all()]

    async def rank(
        self,
        candidates: Sequence[SearchableImage],
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[UUID, float]]:
        if not candidates:
            return []
        candidate_ids = [item.index.id for item in candidates]
        bind = self.db.get_bind()
        if bind.dialect.name == "sqlite":
            query_norm = math.sqrt(sum(value * value for value in query_embedding))
            scores: list[tuple[UUID, float]] = []
            for item in candidates:
                vector = item.index.embedding or []
                vector_norm = math.sqrt(sum(value * value for value in vector))
                score = (
                    sum(left * right for left, right in zip(vector, query_embedding, strict=True))
                    / (vector_norm * query_norm)
                    if vector_norm and query_norm
                    else 0.0
                )
                scores.append((item.index.id, score))
            return sorted(scores, key=lambda item: (-item[1], str(item[0])))[:limit]

        embedding_column = ImageSemanticIndex.__table__.c.embedding
        distance = embedding_column.cosine_distance(query_embedding)
        result = await self.db.execute(
            select(ImageSemanticIndex.id, (1.0 - distance).label("score"))
            .where(
                ImageSemanticIndex.id.in_(candidate_ids),
                ImageSemanticIndex.status == "completed",
                ImageSemanticIndex.embedding.is_not(None),
            )
            .order_by(distance, ImageSemanticIndex.id)
            .limit(limit)
        )
        return [(row[0], float(row[1])) for row in result.all()]
