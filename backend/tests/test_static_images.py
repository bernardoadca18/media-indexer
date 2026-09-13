import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from starlette.datastructures import Headers

from app.api.dependencies import get_embedding_provider, get_image_semantic_provider
from app.models import MediaAsset, MediaProject
from app.schemas.image import ImageProvenanceIn, ImageSearchRequest
from app.services.embedding_provider import FakeEmbeddingProvider
from app.services.image_ingestion import ImageIngestionService
from app.services.image_search import ImageSearchService
from app.services.image_semantic_index import (
    OPERATOR_IMAGE_CANONICAL_TEXT_VERSION,
    OPERATOR_IMAGE_PROVIDER,
    OPERATOR_IMAGE_SEMANTIC_STRATEGY,
    ImageSemanticAttemptsExhaustedError,
    ImageSemanticDimensionError,
    ImageSemanticIndexProcessor,
    ImageSemanticIndexRequest,
    ImageSemanticIndexService,
    OperatorImageSemanticImportRequest,
    build_canonical_image_text,
)
from app.services.image_semantic_provider import (
    FakeImageSemanticProvider,
    ImageSemanticProviderError,
    ImageSemanticResult,
)
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.storage import LocalStorage


def image_bytes(format_name: str, color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), color=color).save(output, format=format_name)
    return output.getvalue()


def upload(content: bytes, filename: str, content_type: str) -> UploadFile:
    return UploadFile(
        io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def direct_image(db, root: Path, *, color: str = "red", project=None):
    if project is None:
        project = MediaProject(name=f"project-{uuid4()}")
        db.add(project)
        await db.commit()
        await db.refresh(project)
    storage = LocalStorage(root)
    result = await ImageIngestionService(
        db,
        storage,
        max_bytes=1_000_000,
        max_pixels=1_000_000,
        max_provenance_characters=10_000,
    ).ingest(
        project.id,
        upload(image_bytes("PNG", color), f"{color}.png", "image/png"),
        ImageProvenanceIn(
            source_owner="studio",
            source_class="official",
            asset_class="official_screenshot",
            collection="screenshots",
            game="generic game",
            declared_metadata={"known_subject": "Declared Hero"},
        ),
    )
    return project, storage, result


@pytest.mark.parametrize(
    ("format_name", "filename", "mime", "expected_format"),
    [
        ("JPEG", "one.jpg", "image/jpeg", "jpeg"),
        ("PNG", "one.png", "image/png", "png"),
        ("WEBP", "one.webp", "image/webp", "webp"),
    ],
)
async def test_image_ingestion_validates_metadata_and_managed_identity(
    client, tmp_path: Path, format_name: str, filename: str, mime: str, expected_format: str
) -> None:
    project = (await client.post("/projects", json={"name": "images"})).json()
    content = image_bytes(format_name)
    response = await client.post(
        f"/projects/{project['id']}/images",
        data={
            "provenance": json.dumps({"asset_class": "screenshot"}),
            "enqueue_semantic_index": "false",
        },
        files={"image": (filename, content, mime)},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    media = payload["media_asset"]
    assert media["media_kind"] == "image"
    assert media["content_type"] == mime
    assert media["format_name"] == expected_format
    assert (media["width"], media["height"]) == (12, 8)
    assert media["duration_seconds"] is None
    assert media["fps"] is None
    assert media["sha256"] == __import__("hashlib").sha256(content).hexdigest()
    assert "storage_uri" not in media and "probe_metadata" not in media
    materialized = await client.get(f"/media-assets/{media['id']}/content")
    assert materialized.content == content


@pytest.mark.parametrize(
    ("content", "filename", "mime"),
    [
        (b"not an image", "broken.jpg", "image/jpeg"),
        (image_bytes("GIF"), "image.gif", "image/gif"),
        (image_bytes("PNG"), "liar.jpg", "image/jpeg"),
    ],
)
async def test_image_ingestion_rejects_corrupt_unsupported_and_mime_mismatch(
    client, content: bytes, filename: str, mime: str
) -> None:
    project = (await client.post("/projects", json={"name": str(uuid4())})).json()
    response = await client.post(
        f"/projects/{project['id']}/images",
        data={"enqueue_semantic_index": "false"},
        files={"image": (filename, content, mime)},
    )
    assert response.status_code == 422


async def test_image_ingestion_is_content_idempotent_and_provenance_immutable(
    client, session_factory, tmp_path: Path
) -> None:
    project = (await client.post("/projects", json={"name": "idempotent"})).json()
    content = image_bytes("PNG")
    data = {
        "provenance": json.dumps({"source_owner": "owner", "asset_class": "screenshot"}),
        "enqueue_semantic_index": "false",
    }
    first = await client.post(
        f"/projects/{project['id']}/images",
        data=data,
        files={"image": ("first.png", content, "image/png")},
    )
    second = await client.post(
        f"/projects/{project['id']}/images",
        data=data,
        files={"image": ("renamed.png", content, "image/png")},
    )
    assert first.json()["media_created"] is True
    assert second.json()["media_created"] is False
    assert first.json()["media_asset"]["id"] == second.json()["media_asset"]["id"]
    conflict = await client.post(
        f"/projects/{project['id']}/images",
        data={
            "provenance": json.dumps({"source_owner": "different"}),
            "enqueue_semantic_index": "false",
        },
        files={"image": ("again.png", content, "image/png")},
    )
    assert conflict.status_code == 409
    provenance = await client.get(f"/media-assets/{first.json()['media_asset']['id']}/provenance")
    assert provenance.status_code == 200
    assert provenance.json()["source_owner"] == "owner"
    async with session_factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.media_kind == "image")
        )
        assert count == 1
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


async def test_image_ingest_api_can_atomically_enqueue_default_semantic_index(client) -> None:
    client.app.dependency_overrides[get_image_semantic_provider] = (
        lambda: FakeImageSemanticProvider()
    )
    client.app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        dimensions=1024
    )
    project = (await client.post("/projects", json={"name": "queued"})).json()
    response = await client.post(
        f"/projects/{project['id']}/images",
        data={"provenance": "{}"},
        files={"image": ("queued.png", image_bytes("PNG"), "image/png")},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["semantic_index"]["status"] == "pending"
    assert payload["semantic_index"]["vision_model"] == "neovids-vision"
    assert payload["job"]["job_type"] == "image_semantic_index"
    assert payload["job"]["session_id"] is None
    detail = await client.get(f"/image-semantic-indexes/{payload['semantic_index']['id']}")
    assert detail.status_code == 200


def semantic_request(*, vision_model: str = "neovids-vision", embedding_model: str = "model"):
    return ImageSemanticIndexRequest(
        vision_provider="fake-vision",
        vision_model=vision_model,
        embedding_provider="fake",
        embedding_model=embedding_model,
        embedding_dimensions=1024,
    )


async def test_semantic_index_is_versioned_deterministic_and_reused(db, tmp_path: Path) -> None:
    _, storage, ingested = await direct_image(db, tmp_path)
    service = ImageSemanticIndexService(db, 3)
    first = await service.ensure(ingested.media.id, semantic_request())
    repeated = await service.ensure(ingested.media.id, semantic_request())
    alternate = await service.ensure(
        ingested.media.id, semantic_request(vision_model="neovids-vision-next")
    )
    assert first.created and first.enqueue
    assert repeated.semantic_index.id == first.semantic_index.id
    assert not repeated.created and not repeated.enqueue
    assert alternate.semantic_index.id != first.semantic_index.id
    assert first.semantic_index.configuration["prompt_version"] == "static_image_factual_v1"
    assert len(first.semantic_index.configuration_fingerprint) == 64

    provider = FakeImageSemanticProvider()
    embeddings = FakeEmbeddingProvider(dimensions=1024)
    await ImageSemanticIndexProcessor(db, storage, provider, embeddings).run(first.job)
    await db.refresh(first.semantic_index)
    assert first.semantic_index.status == "completed"
    assert first.semantic_index.embedding is not None
    assert len(first.semantic_index.embedding) == 1024
    assert first.semantic_index.embedding_provider == "fake"
    assert first.semantic_index.embedding_model == "model"
    assert "Declared known subject: Declared Hero." in first.semantic_index.canonical_search_text
    assert first.semantic_index.semantic_result["subjects"] == ["person"]

    provenance = ingested.provenance
    result = provider.result
    assert build_canonical_image_text(provenance, result) == build_canonical_image_text(
        provenance, result
    )


def operator_semantic_request(
    text: str = "A human-assisted description of an official street scene.",
    *,
    embedding_model: str = "mxbai-embed-large:latest",
) -> OperatorImageSemanticImportRequest:
    return OperatorImageSemanticImportRequest(
        semantic_text=text,
        sidecar_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        embedding_provider="fake",
        embedding_model=embedding_model,
        embedding_dimensions=1024,
    )


async def test_operator_semantic_import_embeds_without_vision_and_is_versioned(
    db, tmp_path: Path
) -> None:
    project, storage, ingested = await direct_image(db, tmp_path)
    service = ImageSemanticIndexService(db, 3)
    first = await service.ensure_operator_import(ingested.media.id, operator_semantic_request())
    repeated = await service.ensure_operator_import(ingested.media.id, operator_semantic_request())
    changed = await service.ensure_operator_import(
        ingested.media.id,
        operator_semantic_request("Changed operator-authored semantic description."),
    )
    changed_embedding = await service.ensure_operator_import(
        ingested.media.id,
        operator_semantic_request(embedding_model="mxbai-embed-large:next"),
    )
    assert first.created and first.enqueue
    assert repeated.semantic_index.id == first.semantic_index.id
    assert not repeated.created and not repeated.enqueue
    assert changed.semantic_index.id != first.semantic_index.id
    assert changed_embedding.semantic_index.id != first.semantic_index.id
    assert first.semantic_index.strategy == OPERATOR_IMAGE_SEMANTIC_STRATEGY
    assert first.semantic_index.vision_provider == OPERATOR_IMAGE_PROVIDER
    assert first.semantic_index.canonical_text_version == OPERATOR_IMAGE_CANONICAL_TEXT_VERSION
    assert first.semantic_index.configuration["image_sha256"] == ingested.media.sha256
    assert first.semantic_index.provider_metadata["vision_invoked"] is False

    vision = FakeImageSemanticProvider(fail=True)
    embeddings = FakeEmbeddingProvider(dimensions=1024)
    registry = ProcessorRegistry(
        {
            "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                current_db, storage, vision, embeddings
            )
        }
    )
    assert await JobExecutor(db, registry).execute(first.job.id)
    await db.refresh(first.semantic_index)
    assert first.semantic_index.status == "completed"
    assert first.semantic_index.semantic_result["semantic_source"] == "human_assisted_import"
    assert "human-assisted description" in first.semantic_index.canonical_search_text
    assert vision.call_count == 0
    assert embeddings.embed_call_count == 1
    search = await ImageSearchService(db, embeddings).search_project(
        project.id, ImageSearchRequest(query="official street scene")
    )
    assert [item.media_id for item in search.results] == [ingested.media.id]
    assert search.results[0].semantic_index.strategy == OPERATOR_IMAGE_SEMANTIC_STRATEGY
    assert search.results[0].semantic_preview.startswith("A human-assisted description")
    assert vision.call_count == 0


async def test_operator_semantic_import_recovers_failed_embedding_without_vision(
    db, tmp_path: Path
) -> None:
    _, storage, ingested = await direct_image(db, tmp_path)
    service = ImageSemanticIndexService(db, 3)
    first = await service.ensure_operator_import(ingested.media.id, operator_semantic_request())
    vision = FakeImageSemanticProvider(fail=True)
    failing = ProcessorRegistry(
        {
            "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                current_db,
                storage,
                vision,
                FakeEmbeddingProvider(dimensions=1024, fail=True),
            )
        }
    )
    assert not await JobExecutor(db, failing).execute(first.job.id)
    resumed = await service.ensure_operator_import(ingested.media.id, operator_semantic_request())
    assert resumed.semantic_index.id == first.semantic_index.id
    assert resumed.enqueue and not resumed.created
    good_embeddings = FakeEmbeddingProvider(dimensions=1024)
    recovery = ProcessorRegistry(
        {
            "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                current_db, storage, vision, good_embeddings
            )
        }
    )
    assert await JobExecutor(db, recovery).execute(resumed.job.id)
    assert vision.call_count == 0


async def test_operator_semantic_import_rejects_mismatched_sidecar_checksum(
    db, tmp_path: Path
) -> None:
    _, _, ingested = await direct_image(db, tmp_path)
    request = operator_semantic_request()
    invalid = request.__class__(**{**request.__dict__, "sidecar_sha256": "0" * 64})
    with pytest.raises(ValueError, match="sidecar SHA-256"):
        await ImageSemanticIndexService(db, 3).ensure_operator_import(ingested.media.id, invalid)


async def test_operator_semantic_import_api_is_truthful_and_idempotent(client) -> None:
    client.app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        dimensions=1024
    )
    project = (await client.post("/projects", json={"name": "operator semantics"})).json()
    content = image_bytes("PNG")
    ingested = await client.post(
        f"/projects/{project['id']}/images",
        data={"provenance": "{}", "enqueue_semantic_index": "false"},
        files={"image": ("one.png", content, "image/png")},
    )
    text_value = "Existing semantic description from a companion Markdown file."
    payload = {
        "version": "operator_semantic_import_v1",
        "semantic_text": text_value,
        "sidecar_sha256": hashlib.sha256(text_value.encode("utf-8")).hexdigest(),
    }
    media_id = ingested.json()["media_asset"]["id"]
    first = await client.post(f"/media-assets/{media_id}/operator-image-semantics", json=payload)
    repeated = await client.post(f"/media-assets/{media_id}/operator-image-semantics", json=payload)
    assert first.status_code == repeated.status_code == 202
    assert first.json()["semantic_index"]["vision_provider"] == "operator_import"
    assert first.json()["semantic_index"]["vision_model"] == "semantic_sidecar"
    assert first.json()["semantic_index"]["configuration"]["semantic_source"] == (
        "human_assisted_import"
    )
    assert first.json()["semantic_index"]["embedding_model"] == "mxbai-embed-large:latest"
    assert first.json()["semantic_index"]["embedding_dimensions"] == 1024
    assert repeated.json()["created"] is False
    assert repeated.json()["semantic_index"]["id"] == first.json()["semantic_index"]["id"]


async def test_provider_and_embedding_failures_are_durable_and_retry_resumes(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory() as db:
        _, storage, ingested = await direct_image(db, tmp_path)
        ensured = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request()
        )
        vision = FakeImageSemanticProvider(fail=True)
        embeddings = FakeEmbeddingProvider(dimensions=1024)
        registry = ProcessorRegistry(
            {
                "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                    current_db, storage, vision, embeddings
                )
            }
        )
        assert not await JobExecutor(db, registry).execute(ensured.job.id)
        await db.refresh(ensured.semantic_index)
        await db.refresh(ensured.job)
        assert ensured.semantic_index.status == "failed"
        assert "fake vision provider failure" in (ensured.semantic_index.error or "")
        assert ingested.media.media_kind == "image"

        vision.fail = False
        retry = await ImageSemanticIndexService(db, 3).ensure(ingested.media.id, semantic_request())
        assert retry.enqueue and retry.semantic_index.id == ensured.semantic_index.id
        assert await JobExecutor(db, registry).execute(retry.job.id)
        await db.refresh(retry.semantic_index)
        assert retry.semantic_index.status == "completed"
        assert retry.job.attempt_count == 2

        second = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request(embedding_model="broken")
        )
        embeddings.fail = True
        assert not await JobExecutor(db, registry).execute(second.job.id)
        assert vision.call_count == 3
        embeddings.fail = False
        resumed = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request(embedding_model="broken")
        )
        assert await JobExecutor(db, registry).execute(resumed.job.id)
        assert (
            vision.call_count == 3
        )  # persisted structured output avoids a second paid vision call


async def test_image_index_rejects_schema_dimension_and_exhausted_retry(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory() as db:
        _, storage, ingested = await direct_image(db, tmp_path)
        invalid = semantic_request()
        invalid = ImageSemanticIndexRequest(
            vision_provider=invalid.vision_provider,
            vision_model=invalid.vision_model,
            embedding_provider=invalid.embedding_provider,
            embedding_model=invalid.embedding_model,
            embedding_dimensions=3,
        )
        with pytest.raises(ImageSemanticDimensionError):
            await ImageSemanticIndexService(db, 1).ensure(ingested.media.id, invalid)

        wrong_output = await ImageSemanticIndexService(db, 2).ensure(
            ingested.media.id, semantic_request(embedding_model="wrong-dimension")
        )
        wrong_registry = ProcessorRegistry(
            {
                "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                    current_db,
                    storage,
                    FakeImageSemanticProvider(),
                    FakeEmbeddingProvider(dimensions=3),
                )
            }
        )
        assert not await JobExecutor(db, wrong_registry).execute(wrong_output.job.id)
        await db.refresh(wrong_output.semantic_index)
        assert wrong_output.semantic_index.semantic_result is not None
        assert wrong_output.semantic_index.embedding is None
        assert "dimensions" in (wrong_output.semantic_index.error or "")

        ensured = await ImageSemanticIndexService(db, 1).ensure(
            ingested.media.id, semantic_request()
        )
        registry = ProcessorRegistry(
            {
                "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                    current_db,
                    storage,
                    FakeImageSemanticProvider(fail=True),
                    FakeEmbeddingProvider(dimensions=1024),
                )
            }
        )
        assert not await JobExecutor(db, registry).execute(ensured.job.id)
        with pytest.raises(ImageSemanticAttemptsExhaustedError):
            await ImageSemanticIndexService(db, 1).ensure(ingested.media.id, semantic_request())


class InvalidVisionProvider:
    name = "fake-vision"

    async def describe(self, content: bytes, *, mime_type: str, model: str):
        raise ImageSemanticProviderError("malformed structured output")


async def test_invalid_structured_output_never_completes_index(
    session_factory, tmp_path: Path
) -> None:
    async with session_factory() as db:
        _, storage, ingested = await direct_image(db, tmp_path)
        ensured = await ImageSemanticIndexService(db, 2).ensure(
            ingested.media.id, semantic_request()
        )
        registry = ProcessorRegistry(
            {
                "image_semantic_index": lambda current_db: ImageSemanticIndexProcessor(
                    current_db,
                    storage,
                    InvalidVisionProvider(),
                    FakeEmbeddingProvider(dimensions=1024),
                )
            }
        )
        assert not await JobExecutor(db, registry).execute(ensured.job.id)
        await db.refresh(ensured.semantic_index)
        assert ensured.semantic_index.status == "failed"
        assert ensured.semantic_index.semantic_result is None
        assert ensured.semantic_index.embedding is None


async def test_image_search_ranks_filters_isolates_projects_and_has_no_time(
    db, tmp_path: Path
) -> None:
    project, storage, red = await direct_image(db, tmp_path / "one", color="red")
    _, _, blue = await direct_image(db, tmp_path / "one", color="blue", project=project)
    other_project, other_storage, other = await direct_image(db, tmp_path / "two", color="green")
    provider = FakeImageSemanticProvider(
        ImageSemanticResult(
            summary="A nighttime city street",
            description="Traffic and bright signs line a city street.",
            environment=["city"],
            vehicles=["cars"],
            search_terms=["night", "traffic"],
        )
    )
    embeddings = FakeEmbeddingProvider(dimensions=1024)
    completed = []
    for ingested, current_storage in (
        (red, storage),
        (blue, storage),
        (other, other_storage),
    ):
        ensured = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request()
        )
        await ImageSemanticIndexProcessor(db, current_storage, provider, embeddings).run(
            ensured.job
        )
        completed.append(ensured.semantic_index)
    completed[0].embedding = [1.0] + [0.0] * 1023
    completed[1].embedding = [0.0, 1.0] + [0.0] * 1022
    completed[2].embedding = [1.0] + [0.0] * 1023
    await db.commit()

    query_provider = FakeEmbeddingProvider(dimensions=1024)
    response = await ImageSearchService(db, query_provider).search_project(
        project.id,
        ImageSearchRequest(
            query="night traffic",
            filters={"asset_class": "official_screenshot", "source_owner": "studio"},
        ),
    )
    assert [item.media_id for item in response.results] == [red.media.id, blue.media.id]
    assert response.results[0].score > response.results[1].score
    assert all(item.project_id == project.id for item in response.results)
    assert query_provider.embed_call_count == 1
    serialized = response.model_dump(mode="json")
    assert "start_seconds" not in json.dumps(serialized)
    assert "end_seconds" not in json.dumps(serialized)
    assert response.results[0].content_url.endswith(f"/{red.media.id}/content")

    zero = await ImageSearchService(db, query_provider).search_project(
        other_project.id,
        ImageSearchRequest(query="anything", filters={"asset_class": "excluded"}),
    )
    assert zero.results == []


async def test_image_search_embeds_once_per_compatibility_group(db, tmp_path: Path) -> None:
    project, storage, ingested = await direct_image(db, tmp_path)
    vision = FakeImageSemanticProvider()
    setup_embedding = FakeEmbeddingProvider(dimensions=1024)
    for model in ("embedding-a", "embedding-b"):
        ensured = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request(embedding_model=model)
        )
        await ImageSemanticIndexProcessor(db, storage, vision, setup_embedding).run(ensured.job)
    query_provider = FakeEmbeddingProvider(dimensions=1024)
    response = await ImageSearchService(db, query_provider).search_project(
        project.id, ImageSearchRequest(query="urban street")
    )
    assert response.compatibility_groups == 2
    assert query_provider.embed_call_count == 2
    assert len(response.results) == 1  # versions coexist; result is deduplicated by MediaAsset


@pytest.mark.postgres
async def test_postgres_image_vector_search_and_provenance_immutability(
    postgres_db, tmp_path: Path
) -> None:
    db = postgres_db
    project, storage, red = await direct_image(db, tmp_path, color="red")
    _, _, blue = await direct_image(db, tmp_path, color="blue", project=project)
    vision = FakeImageSemanticProvider()
    setup = FakeEmbeddingProvider(dimensions=1024)
    indexes = []
    for ingested in (red, blue):
        ensured = await ImageSemanticIndexService(db, 3).ensure(
            ingested.media.id, semantic_request()
        )
        await ImageSemanticIndexProcessor(db, storage, vision, setup).run(ensured.job)
        indexes.append(ensured.semantic_index)
    indexes[0].embedding = [1.0] + [0.0] * 1023
    indexes[1].embedding = [0.0, 1.0] + [0.0] * 1022
    await db.commit()
    response = await ImageSearchService(db, FakeEmbeddingProvider(dimensions=1024)).search_project(
        project.id, ImageSearchRequest(query="red")
    )
    assert [result.media_id for result in response.results] == [red.media.id, blue.media.id]

    indexes = {
        row.indexname: row.indexdef
        for row in (
            await db.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename='media_asset_provenance'"
                )
            )
        )
    }
    assert "(project_id, asset_class)" in indexes["ix_media_asset_provenance_project_asset_class"]
    assert "(project_id, source_class)" in indexes["ix_media_asset_provenance_project_source_class"]

    for field, value in {
        "source_owner": "studio",
        "source_class": "official",
        "asset_class": "official_screenshot",
        "collection": "screenshots",
        "game": "generic game",
    }.items():
        filtered = await ImageSearchService(
            db, FakeEmbeddingProvider(dimensions=1024)
        ).search_project(
            project.id,
            ImageSearchRequest(query="red", filters={field: value}),
        )
        assert {item.media_id for item in filtered.results} == {
            red.media.id,
            blue.media.id,
        }

    red.provenance.source_owner = "silently changed"
    with pytest.raises(DBAPIError, match="provenance is immutable"):
        await db.commit()
    await db.rollback()
