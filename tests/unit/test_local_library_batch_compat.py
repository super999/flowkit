from unittest.mock import AsyncMock

import pytest

from agent.services.flow_client import FlowClient
from agent.services import flow_batch as fb
from agent.db import crud


@pytest.mark.asyncio
async def test_linked_projects_keep_dashboard_shape(monkeypatch):
    monkeypatch.setattr(crud, "list_projects", AsyncMock(return_value=[{"id":"p", "name":"Existing"}]))
    client = FlowClient()
    assert (await client.get_project("p")) == {"projectId":"p", "title":"Existing", "source":"local_link"}
    assert await client.get_project("absent") == {}


@pytest.mark.asyncio
async def test_listing_preserves_original_prompt_and_detects_uncached_video(monkeypatch):
    mid = "11111111-1111-1111-1111-111111111111"
    video = "22222222-2222-2222-2222-222222222222"
    op = "33333333-3333-3333-3333-333333333333"
    payload = [[op, None, None, ["generated title", None, None, None, mid]],
               [op, None, None, ["another title", None, None, None, video]]]
    monkeypatch.setattr(crud, "list_media_library", AsyncMock(return_value=([
        {"media_id":mid,"prompt":"原始中文提示词","model_name":"NARWHAL","media_type":"IMAGE"}],1)))
    client = FlowClient()
    client._batch_payload = AsyncMock(return_value=payload)
    client._batch_media_urls = AsyncMock(return_value=fb.MediaUrls(media_id=video,video="https://flow-content.google/video/v",image=None))
    rows = await client.fetch_project_media("p")
    assert rows[0]["prompt"] == "原始中文提示词"
    assert rows[0]["modelName"] == "NARWHAL"
    assert rows[1]["mediaType"] == "VIDEO"
    assert rows[1]["prompt"] == ""  # Never present a generated title as the original prompt.
