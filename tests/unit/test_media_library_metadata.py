import asyncio

import aiosqlite
import pytest

from agent.db import crud
from agent.services import media_sync
from unittest.mock import AsyncMock


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_sparse_sync_preserves_metadata_and_recovers_cloud_date(monkeypatch, batch):
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""CREATE TABLE media_library (
            media_id TEXT PRIMARY KEY, project_id TEXT, project_title TEXT,
            name TEXT, prompt TEXT, translated_prompt TEXT, model_name TEXT,
            aspect_ratio TEXT, media_type TEXT, url TEXT, thumb TEXT,
            local_path TEXT, is_cached INTEGER, source TEXT,
            created_at TEXT, updated_at TEXT)""")

        async def get_db():
            return db

        monkeypatch.setattr(crud, "get_db", get_db)
        monkeypatch.setattr(crud, "_db_lock", asyncio.Lock())

        async def save(**item):
            if batch:
                await crud.batch_upsert_media_library([item])
            else:
                await crud.upsert_media_library_item(**item)

        await save(media_id="image", prompt="原始提示词", name="原名",
                   model_name="NARWHAL", url="https://example.test/image",
                   is_cached=1, local_path="/output/image.jpg",
                   created_at="2026-09-20T00:00:00Z")
        await save(media_id="image", prompt="", name="", model_name="",
                   url="", local_path="", is_cached=0)
        row = await (await db.execute("SELECT * FROM media_library")).fetchone()
        assert row["prompt"] == "原始提示词"
        assert row["name"] == "原名"
        assert row["url"] == "https://example.test/image"
        assert row["model_name"] == "NARWHAL"
        assert row["is_cached"] == 1
        assert row["local_path"] == "/output/image.jpg"
        assert row["created_at"] == "2026-09-20T00:00:00Z"
        await save(media_id="image", created_at="2026-09-14T00:00:00Z")
        row = await (await db.execute("SELECT * FROM media_library")).fetchone()
        assert row["created_at"] == "2026-09-14T00:00:00Z"


@pytest.mark.asyncio
async def test_cache_button_retries_html_previously_marked_cached(monkeypatch, tmp_path):
    (tmp_path / "bad.jpg").write_bytes(b"<!doctype html><title>Sign in</title>")
    (tmp_path / "good.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"image" * 20)
    monkeypatch.setattr(media_sync, "MEDIA_CACHE_DIR", tmp_path)
    monkeypatch.setattr(media_sync, "_is_caching", False)
    monkeypatch.setattr(crud, "list_media_library", AsyncMock(return_value=([
        {"media_id": "bad", "is_cached": 1, "media_type": "IMAGE"},
        {"media_id": "good", "is_cached": 1, "media_type": "IMAGE"},
    ], 2)))
    update = AsyncMock()
    cache = AsyncMock(return_value="/output/_cache/bad.jpg")
    monkeypatch.setattr(crud, "update_media_library_item", update)
    monkeypatch.setattr(media_sync, "cache_media_item", cache)
    result = await media_sync.cache_all_uncached()
    assert result["total"] == 1
    update.assert_awaited_once_with("bad", is_cached=0, local_path=None)
    cache.assert_awaited_once_with("bad", url=None, media_type="IMAGE")
