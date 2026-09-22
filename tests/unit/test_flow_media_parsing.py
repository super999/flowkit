import copy
import uuid
from unittest.mock import AsyncMock

import pytest

from agent.db import crud
from agent.services import flow_batch as fb
from agent.services.flow_client import (
    FlowClient,
    extract_prompt_pair_from_media,
)


def _wire_media_payload():
    project_id = "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb"
    workflow_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    media_id = "658875db-88f1-4dda-b35a-fd74663c0924"
    original = "一张木质古典展示台的正面设计图"
    translated = "Front view of a classic wooden display stand"
    image_url = f"https://flow-content.google/image/{media_id}?x=1"

    # Current Flow's positional media shape.  Prompt and translation indexes
    # mirror the live Zzl0ze response while keeping the fixture short.
    prompt_meta = [None, None, [[original, None, []]], None, 1]
    meta = [[1789904776, 417817000], None, None, None, None, image_url, prompt_meta,
            None, None, None, image_url]
    details0 = [None] * 15
    details0[7] = translated
    details0[14] = 1
    details = [details0, None, [1024, 1024]]
    media = [media_id, project_id, workflow_id, "CAE", None, meta, details]

    # Workflow title is separate metadata; it must not overwrite the original
    # prompt when parsing a media record.
    workflow = [workflow_id, project_id, None, ["镜头 06.png", None, None, None, media_id]]
    return project_id, media_id, original, translated, [None, [workflow], [media]]


def _wire_video_payload():
    project_id = "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb"
    workflow_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    media_id = "61b61354-3a91-45c2-a174-643f526e98de"
    original = "镜头缓慢推进，展示古典木质展台上的玉器"
    translated = "Slow camera push toward jade objects on a classic wooden display stand"
    video_url = f"https://flow-content.google/video/{media_id}?sig=signed"
    poster_url = f"https://flow-content.google/image/{media_id}?sig=poster"

    meta = [None] * 11
    meta[0] = [1789904776, 417817000]
    meta[1] = original
    meta[6] = [None, [["abra_i2v_6s", 2, [1], None, 2, 1], []],
               [[None, None, [[[original]]]]], None, 1]
    meta[10] = poster_url

    video_detail = [None] * 17
    video_detail[7] = translated
    video_detail[8] = video_url
    video_detail[12] = "abra_i2v_6s"
    video_detail[16] = 2
    video_details = [video_detail, [None, None, [6]]]
    media = [media_id, project_id, workflow_id, "CAE", None, meta, None, video_details]
    workflow = [workflow_id, project_id, None, ["video-shot", None, None, None, media_id]]
    return project_id, media_id, original, translated, video_url, poster_url, [None, [workflow], [media]]


def test_extract_wire_prompt_pair_keeps_original_and_translation():
    _, _, original, translated, payload = _wire_media_payload()
    media = payload[2][0]
    assert extract_prompt_pair_from_media(media) == (original, translated)


@pytest.mark.asyncio
async def test_fetch_project_media_reads_payload_media_records(monkeypatch):
    project_id, media_id, original, translated, payload = _wire_media_payload()
    monkeypatch.setattr(
        crud,
        "list_media_library",
        AsyncMock(return_value=([], 0)),
    )
    client = FlowClient()
    client._batch_payload = AsyncMock(return_value=payload)
    client._batch_media_urls = AsyncMock(
        return_value=fb.MediaUrls(media_id=media_id, image=None, video=None)
    )

    rows = await client.fetch_project_media(project_id)

    assert len(rows) == 1
    assert rows[0]["mediaKey"] == media_id
    assert rows[0]["prompt"] == original
    assert rows[0]["translated_prompt"] == translated
    assert rows[0]["mediaType"] == "IMAGE"
    assert rows[0]["aspectRatio"] == "IMAGE_ASPECT_RATIO_SQUARE"
    assert rows[0]["url"].startswith("https://flow-content.google/image/")
    client._batch_media_urls.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_project_media_reads_video_details_at_slot_seven(monkeypatch):
    project_id, media_id, original, translated, video_url, poster_url, payload = _wire_video_payload()
    monkeypatch.setattr(crud, "list_media_library", AsyncMock(return_value=([], 0)))
    client = FlowClient()
    client._batch_payload = AsyncMock(return_value=payload)

    rows = await client.fetch_project_media(project_id)

    assert len(rows) == 1
    assert rows[0]["mediaKey"] == media_id
    assert rows[0]["mediaType"] == "VIDEO"
    assert rows[0]["prompt"] == original
    assert rows[0]["translated_prompt"] == translated
    assert rows[0]["modelName"] == "abra_i2v_6s"
    assert rows[0]["aspectRatio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    assert rows[0]["url"] == video_url
    assert rows[0]["url"] != poster_url


@pytest.mark.asyncio
async def test_fetch_project_media_does_not_apply_200_item_cap(monkeypatch):
    project_id, _, _, _, payload = _wire_media_payload()
    template = payload[2][0]
    entries = []
    for index in range(205):
        entry = copy.deepcopy(template)
        entry[0] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"flowkit-media-{index}"))
        entries.append(entry)
    payload[2] = entries
    payload[1] = []

    monkeypatch.setattr(crud, "list_media_library", AsyncMock(return_value=([], 0)))
    client = FlowClient()
    client._batch_payload = AsyncMock(return_value=payload)

    rows_default = await client.fetch_project_media(project_id)
    rows_unlimited = await client.fetch_project_media(project_id, limit=None)

    assert len(rows_default) == 205
    assert len(rows_unlimited) == 205
    assert rows_default[-1]["mediaKey"] == entries[-1][0]
    assert rows_unlimited[-1]["mediaKey"] == entries[-1][0]


@pytest.mark.asyncio
async def test_fetch_project_media_keeps_media_url_over_reference_url(monkeypatch):
    project_id, media_id, _, _, payload = _wire_media_payload()
    entry = payload[2][0]
    media_url = entry[5][5]
    reference_url = "https://flow-content.google/image/reference-only?x=1"
    entry[5][6][2][0][2] = [reference_url]

    monkeypatch.setattr(crud, "list_media_library", AsyncMock(return_value=([], 0)))
    client = FlowClient()
    client._batch_payload = AsyncMock(return_value=payload)

    rows = await client.fetch_project_media(project_id)

    assert rows[0]["mediaKey"] == media_id
    assert rows[0]["url"] == media_url
    assert rows[0]["url"] != reference_url


@pytest.mark.asyncio
async def test_resolve_media_url_uses_signed_batch_media_url():
    media_id = "61b61354-3a91-45c2-a174-643f526e98de"
    signed_url = f"https://flow-content.google/image/{media_id}?sig=signed"
    client = FlowClient()
    client._batch_media_urls = AsyncMock(
        return_value=fb.MediaUrls(media_id=media_id, image=signed_url, video=None)
    )

    assert await client.resolve_media_url(media_id) == signed_url
    client._batch_media_urls.assert_awaited_once_with(media_id)


@pytest.mark.asyncio
async def test_resolve_media_urls_returns_successful_id_to_url_mapping():
    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    client = FlowClient()
    async def fake_resolve(media_id, timeout=60):
        return {
            first: "https://flow-content.google/image/first?sig=signed",
            second: None,
        }.get(media_id)

    client.resolve_media_url = AsyncMock(side_effect=fake_resolve)

    assert await client.resolve_media_urls([first, second, first]) == {
        first: "https://flow-content.google/image/first?sig=signed",
    }
    assert client.resolve_media_url.await_count == 2


def test_upload_record_without_prompt_does_not_use_workflow_title():
    project_id, media_id, _, _, payload = _wire_media_payload()
    upload = payload[2][0][:]
    upload[5] = [upload[5][0], None, None, None, None, "https://lh3.googleusercontent.com/upload", [None, None, None, None, 1]]
    upload[6] = [None, [None, None, None, "https://lh3.googleusercontent.com/upload", 0], [1024, 1024]]
    assert extract_prompt_pair_from_media(upload, payload[1][0]) == ("", None)
