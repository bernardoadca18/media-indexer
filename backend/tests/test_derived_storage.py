import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.storage import LocalStorage


async def test_derived_asset_is_atomic_hashed_resolvable_and_deletable(
    tmp_path: Path,
) -> None:
    project_id, session_id, visual_index_id = uuid4(), uuid4(), uuid4()
    storage = LocalStorage(tmp_path)
    first = await storage.save_derived(
        project_id, session_id, "visual", visual_index_id, "frames", "frame.jpg", b"one"
    )
    replaced = await storage.save_derived(
        project_id, session_id, "visual", visual_index_id, "frames", "frame.jpg", b"two"
    )

    assert replaced.uri == first.uri
    assert replaced.byte_size == 3
    assert replaced.sha256 == hashlib.sha256(b"two").hexdigest()
    assert storage.resolve(replaced.uri).read_bytes() == b"two"
    assert not list(storage.resolve(replaced.uri).parent.glob("*.tmp"))

    await storage.delete_derived_namespace(project_id, session_id, "visual", visual_index_id)
    assert not storage.resolve(replaced.uri).exists()


async def test_derived_storage_rejects_unsafe_path_components(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValueError, match="unsafe storage path component"):
        await storage.save_derived(
            uuid4(), uuid4(), "../visual", uuid4(), "frames", "frame.jpg", b"jpeg"
        )
