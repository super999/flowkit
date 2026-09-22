"""Regression tests for Flow media synchronization.

These tests keep the Flow client and database at the boundary.  They verify
the sync service's handling of the response shapes and failure reporting that
the browser-facing API relies on.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.services import media_sync
from agent.db import crud


PROJECT_ID = "11111111-2222-3333-4444-555555555555"
MEDIA_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _EmptyCursor:
    async def fetchall(self):
        return []


class _EmptyDb:
    async def execute(self, *_args, **_kwargs):
        return _EmptyCursor()

    async def commit(self):
        return None


@pytest.fixture(autouse=True)
def reset_sync_state():
    media_sync._is_syncing = False
    yield
    media_sync._is_syncing = False


def _patch_db(monkeypatch, existing=None):
    monkeypatch.setattr(crud, "get_db", AsyncMock(return_value=_EmptyDb()))
    monkeypatch.setattr(
        crud,
        "list_media_library",
        AsyncMock(return_value=(existing or [], len(existing or []))),
    )


class _Client:
    connected = True

    def __init__(self, projects, media=None, error=None):
        self.projects = projects
        self.media = media or []
        self.error = error
        self.limit = "unset"

    async def search_user_projects(self, limit=100):
        return self.projects

    async def fetch_project_media(self, project_id, limit=None):
        self.limit = limit
        if self.error:
            raise self.error
        return self.media


@pytest.mark.asyncio
async def test_sync_uses_top_level_project_title_and_fetches_complete_listing(monkeypatch):
    client = _Client(
        [{"projectId": PROJECT_ID, "title": "巴图-01"}],
        [{"mediaKey": MEDIA_ID, "mediaType": "IMAGE", "prompt": "原始提示词"}],
    )
    monkeypatch.setattr(media_sync, "get_flow_client", lambda: client)
    monkeypatch.setattr(crud, "get_project", AsyncMock(return_value=None))
    _patch_db(monkeypatch)
    captured = []

    async def upsert(items):
        captured.extend(items)
        return len(items)

    monkeypatch.setattr(crud, "batch_upsert_media_library", upsert)

    result = await media_sync.sync_flow_media(auto_cache=False)

    assert result["status"] == "success"
    assert result["synced"] == 1
    assert client.limit is None
    assert captured[0]["project_title"] == "巴图-01"
    assert captured[0]["prompt"] == "原始提示词"


@pytest.mark.asyncio
async def test_sync_preserves_existing_fields_when_flow_omits_them(monkeypatch):
    existing = {
        "media_id": MEDIA_ID,
        "project_title": "已有项目",
        "prompt": "已有原始提示词",
        "translated_prompt": "existing translated prompt",
        "model_name": "NARWHAL",
        "aspect_ratio": "IMAGE_ASPECT_RATIO_SQUARE",
        "media_type": "IMAGE",
        "url": "https://old.example/image.jpg",
        "thumb": "https://old.example/image.jpg",
        "name": "已有原始提示词",
        "created_at": "2026-09-01T00:00:00Z",
    }
    client = _Client(
        [{"projectId": PROJECT_ID, "title": "项目标题"}],
        [{"mediaKey": MEDIA_ID, "mediaType": "IMAGE", "title": "Flow generated title"}],
    )
    monkeypatch.setattr(media_sync, "get_flow_client", lambda: client)
    monkeypatch.setattr(crud, "get_project", AsyncMock(return_value=None))
    _patch_db(monkeypatch, [existing])
    captured = []

    async def upsert(items):
        captured.extend(items)
        return len(items)

    monkeypatch.setattr(crud, "batch_upsert_media_library", upsert)

    result = await media_sync.sync_flow_media(auto_cache=False)

    item = captured[0]
    assert result["status"] == "success"
    assert item["prompt"] == "已有原始提示词"
    assert item["translated_prompt"] is None
    assert item["model_name"] == "NARWHAL"
    assert item["aspect_ratio"] == "IMAGE_ASPECT_RATIO_SQUARE"
    assert item["url"] == "https://old.example/image.jpg"
    assert item["name"] == "已有原始提示词"


@pytest.mark.asyncio
async def test_sync_reports_project_failures_instead_of_returning_success(monkeypatch):
    client = _Client(
        [{"projectId": PROJECT_ID, "title": "项目标题"}],
        error=RuntimeError("Zzl0ze failed"),
    )
    monkeypatch.setattr(media_sync, "get_flow_client", lambda: client)
    monkeypatch.setattr(crud, "get_project", AsyncMock(return_value=None))
    _patch_db(monkeypatch)

    result = await media_sync.sync_flow_media(auto_cache=False)

    assert result["status"] == "error"
    assert result["projects_failed"] == 1
    assert "Zzl0ze failed" in result["errors"][0]


@pytest.mark.asyncio
async def test_sync_timeout_is_reported(monkeypatch):
    class SlowClient(_Client):
        async def fetch_project_media(self, project_id, limit=None):
            await asyncio.sleep(0.05)
            return []

    client = SlowClient([{"projectId": PROJECT_ID, "title": "项目标题"}])
    monkeypatch.setattr(media_sync, "get_flow_client", lambda: client)
    monkeypatch.setattr(crud, "get_project", AsyncMock(return_value=None))
    _patch_db(monkeypatch)
    monkeypatch.setattr(media_sync, "PROJECT_SYNC_TIMEOUT_SECONDS", 0.001)

    result = await media_sync.sync_flow_media(auto_cache=False)

    assert result["status"] == "error"
    assert result["projects_failed"] == 1
    assert "超时" in result["errors"][0]


def test_project_title_ignores_uuid_fallback():
    assert media_sync._project_title(
        {"projectId": PROJECT_ID, "title": PROJECT_ID},
        fallback="",
    ) == ""
    assert media_sync._project_title(
        {"projectId": PROJECT_ID, "projectInfo": {"projectTitle": "原始标题"}},
    ) == "原始标题"


@pytest.mark.asyncio
async def test_repair_prompts_fetches_complete_projects_and_reports_prompt(monkeypatch):
    client = _Client(
        [{"projectId": PROJECT_ID, "title": "项目标题"}],
        [{"mediaKey": MEDIA_ID, "prompt": "Here's the English translation: restored prompt"}],
    )
    monkeypatch.setattr(media_sync, "get_flow_client", lambda: client)
    monkeypatch.setattr(crud, "get_db", AsyncMock(return_value=_EmptyDb()))
    result = await media_sync.repair_flow_prompts()

    assert result["status"] == "success"
    assert result["repaired"] == 1
    assert client.limit is None
