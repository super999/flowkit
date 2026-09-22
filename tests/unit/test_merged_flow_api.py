"""Compatibility checks for the merged direct Flow API surface."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.api import flow


PROJECT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.asyncio
async def test_generate_image_forwards_variants_seed_and_merged_references(monkeypatch):
    client = SimpleNamespace(
        connected=True,
        generate_images=AsyncMock(return_value={"status": 200, "data": {"media": []}}),
    )
    monkeypatch.setattr(flow, "get_flow_client", lambda: client)

    body = flow.GenerateImageRequest(
        prompt="a jade pavilion",
        project_id=PROJECT_ID,
        image_model="NARWHAL",
        count=3,
        seed=42,
        reference_media_ids=["ref-a", "ref-b", "ref-a"],
        character_media_ids=["ref-b", "char-c"],
    )
    result = await flow.generate_image(body)

    assert result == {"media": []}
    kwargs = client.generate_images.await_args.kwargs
    assert kwargs["count"] == 3
    assert kwargs["seed"] == 42
    assert kwargs["image_model"] == "NARWHAL"
    assert kwargs["character_media_ids"] == ["ref-a", "ref-b", "char-c"]


def test_local_and_official_routes_are_both_registered():
    paths = [route.path for route in flow.router.routes]
    for path in (
        "/flow/generate-image",
        "/flow/generate-video-omni-text",
        "/flow/generate-video-omni",
        "/flow/export-image",
        "/flow/upscale-image",
        "/flow/media/image/download",
        "/flow/upload-image-data",
        "/flow/upload-image",
        "/flow/media/resolve",
        "/flow/media/proxy",
    ):
        assert path in paths

    # Static media endpoints must precede /media/{media_id}; otherwise
    # GET /media/proxy is dispatched to get_media("proxy").
    assert paths.index("/flow/media/proxy") < paths.index("/flow/media/{media_id}")


@pytest.mark.asyncio
async def test_media_proxy_prefers_a_cached_file(monkeypatch, tmp_path):
    cached = tmp_path / "media.jpg"
    cached.write_bytes(b"\xff\xd8\xff\xe0cached")
    monkeypatch.setattr(
        "agent.services.media_cache.get_cached_image_path",
        lambda _media_id: cached,
    )

    response = await flow.proxy_media_image(media_id="media")

    assert response.path == str(cached)


@pytest.mark.asyncio
async def test_upscale_json_and_export_binary_compatibility(monkeypatch):
    encoded = base64.b64encode(b"jpeg-bytes").decode()
    client = SimpleNamespace(
        connected=True,
        upscale_image=AsyncMock(
            return_value={
                "status": 200,
                "data": {
                    "media_id": "upscaled-id",
                    "encodedImage": encoded,
                    "contentType": "image/jpeg",
                },
            }
        ),
    )
    monkeypatch.setattr(flow, "get_flow_client", lambda: client)

    json_result = await flow.upscale_image(
        flow.UpscaleImageRequest(
            media_id="source-id",
            project_id=PROJECT_ID,
            target_resolution="UPSAMPLE_IMAGE_RESOLUTION_4K",
        )
    )
    assert json_result["media_id"] == "upscaled-id"
    assert json_result["base64"] == encoded
    assert json_result["resolution"] == "UPSAMPLE_IMAGE_RESOLUTION_4K"
    assert client.upscale_image.await_args.kwargs["resolution"] == "UPSAMPLE_IMAGE_RESOLUTION_4K"

    client.upscale_image.reset_mock()
    binary_response = await flow.export_image(
        flow.UpscaleImageRequest(
            media_id="source-id",
            project_id=PROJECT_ID,
            quality="2k",
        )
    )
    assert binary_response.body == b"jpeg-bytes"
    assert binary_response.media_type == "image/jpeg"
    assert client.upscale_image.await_args.kwargs["resolution"] == "2K"
