"""Direct Flow API endpoints — for manual operations outside the queue."""
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from typing import Literal, Optional

from agent.config import USE_BATCH_RPC, FLOW_PROJECT_ID, FLOW_ALLOW_DEGRADED
from agent.services.flow_client import get_flow_client
from agent.services.omni_flash import (
    check_omni_flash_status,
    generate_omni_flash_first_frame_video,
    generate_omni_flash_first_last_video,
    generate_omni_flash_video,
)

router = APIRouter(prefix="/flow", tags=["flow"])


class GenerateImageRequest(BaseModel):
    prompt: str
    project_id: str
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT"
    user_paygate_tier: str = "PAYGATE_TIER_ONE"
    character_media_ids: Optional[list[str]] = None
    image_model: Optional[str] = None  # override model, e.g. GEM_PIX_2_UPSAMPLE_2K/_4K
    source_media_id: Optional[str] = None  # upscale/edit source image


class GenerateVideoRequest(BaseModel):
    start_image_media_id: str
    prompt: str
    project_id: str
    scene_id: str
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT"
    end_image_media_id: Optional[str] = None
    user_paygate_tier: str = "PAYGATE_TIER_ONE"
    # Backward compatible: legacy requests remain Veo unless explicitly set.
    model_family: Literal["veo", "omni_flash"] = "veo"
    duration_s: int = 8


class GenerateVideoRefsRequest(BaseModel):
    reference_media_ids: list[str]
    prompt: str
    project_id: str
    scene_id: str
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT"
    user_paygate_tier: str = "PAYGATE_TIER_ONE"
    # Backward compatible: existing callers keep the Veo R2V path unless they
    # explicitly opt into Omni Flash.
    model_family: Literal["veo", "omni_flash"] = "veo"
    duration_s: int = 8


class GenerateOmniFlashVideoRequest(BaseModel):
    reference_media_ids: list[str]
    prompt: str
    project_id: str
    scene_id: str = ""
    duration_s: int = 8
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT"
    user_paygate_tier: str = "PAYGATE_TIER_ONE"


class UpscaleVideoRequest(BaseModel):
    media_id: str
    scene_id: str
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT"
    resolution: str = "VIDEO_RESOLUTION_4K"


class UploadImageRequest(BaseModel):
    file_path: str  # absolute path to local image file
    project_id: str = ""
    file_name: str = "image.png"


class CheckStatusRequest(BaseModel):
    operations: list[dict] = []
    # Omni/workflow-mode callers should pass workflow descriptors instead of
    # operation handles. If workflows is set, /check-status automatically uses
    # authenticated Flow project polling.
    workflows: Optional[list[dict]] = None
    project_id: str = ""
    include_encoded_video: bool = False


class CheckOmniStatusRequest(BaseModel):
    workflows: list[dict]
    project_id: str = ""
    include_encoded_video: bool = False


class EditImageRequest(BaseModel):
    prompt: str
    source_media_id: str
    project_id: str
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT"
    user_paygate_tier: str = "PAYGATE_TIER_ONE"


@router.get("/status")
async def extension_status():
    """Extension health, and which transport it is being asked to speak.

    `flow_key_present` is a legacy-path signal: the batchexecute path has no
    bearer token at all, so false is expected there rather than a fault.
    """
    client = get_flow_client()
    return {
        "connected": client.connected,
        "transport": "batch" if USE_BATCH_RPC else "legacy_rest",
        "flow_project_id": FLOW_PROJECT_ID or None,
        "allow_degraded": FLOW_ALLOW_DEGRADED,
        "flow_key_present": client._flow_key is not None,
    }


class TrpcProxyRequest(BaseModel):
    procedure: str  # e.g. "project.getProject"
    input: dict = {}  # tRPC input JSON
    method: str = "GET"  # GET or POST


@router.post("/trpc-proxy")
async def trpc_proxy(req: TrpcProxyRequest):
    """Generic tRPC proxy — send any tRPC call through the extension.

    Useful for reverse-engineering Flow API endpoints.
    """
    import urllib.parse
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    if req.method.upper() == "GET":
        encoded = urllib.parse.quote(json.dumps(req.input))
        url = f"https://labs.google/fx/api/trpc/{req.procedure}?input={encoded}"
        result = await client._send("trpc_request", {
            "url": url,
            "method": "GET",
            "headers": {"content-type": "application/json"},
        }, timeout=30)
    else:
        url = f"https://labs.google/fx/api/trpc/{req.procedure}"
        result = await client._send("trpc_request", {
            "url": url,
            "method": "POST",
            "headers": {"content-type": "application/json"},
            "body": req.input,
        }, timeout=30)

    return result


@router.get("/projects")
async def list_flow_projects():
    """List all user projects from Google Flow."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    projects = await client.search_user_projects()
    return {"projects": projects}


@router.get("/projects/{project_id}")
async def get_flow_project(project_id: str):
    """Get a single project's metadata from Google Flow."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    project = await client.get_project(project_id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    return project



@router.get("/projects/{project_id}/media")
async def get_project_media(project_id: str, limit: int = 20):
    """Get all media (images/videos) for a Google Flow project.

    Note: the underlying media.fetchUserHistory API rejects limit > 20.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    media = await client.fetch_project_media(project_id, limit=min(limit, 200))
    return {"project_id": project_id, "total": len(media), "media": media}


@router.get("/history")
async def get_flow_history(limit: int = 100, history_type: str = "FLOW"):
    """Get user's media generation history from Google Flow."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    workflows = await client.fetch_user_history(limit=limit, history_type=history_type)
    return {"total": len(workflows), "workflows": workflows}


@router.get("/credits")
async def get_credits():
    """Get user credits from Google Flow."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.get_credits()
    if result.get("error"):
        raise HTTPException(502, result["error"])
    return result.get("data", result)


@router.post("/generate-image")
async def generate_image(body: GenerateImageRequest):
    """Generate image directly (bypasses queue)."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.generate_images(**body.model_dump(exclude_none=True))
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


@router.post("/generate-video")
async def generate_video(body: GenerateVideoRequest):
    """Submit frame-conditioned video generation using Veo or Omni Flash.

    Existing callers default to Veo. For Omni set ``model_family=omni_flash``
    and ``duration_s`` to 4/6/8/10. With only ``start_image_media_id`` the
    request uses Omni First frame. When ``end_image_media_id`` is also present,
    it uses Omni First+Last frames.

    Omni responses include ``flowkitPolling.workflows`` and must use workflow
    media polling rather than legacy operation polling.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    if body.model_family == "omni_flash":
        try:
            common = dict(
                start_image_media_id=body.start_image_media_id,
                prompt=body.prompt,
                project_id=body.project_id,
                scene_id=body.scene_id,
                duration_s=body.duration_s,
                aspect_ratio=body.aspect_ratio,
                user_paygate_tier=body.user_paygate_tier,
            )
            if body.end_image_media_id:
                result = await generate_omni_flash_first_last_video(
                    end_image_media_id=body.end_image_media_id,
                    **common,
                )
            else:
                result = await generate_omni_flash_first_frame_video(**common)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        result = await client.generate_video(
            **body.model_dump(exclude={"model_family", "duration_s"}, exclude_none=True)
        )

    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


@router.post("/generate-video-refs")
async def generate_video_refs(body: GenerateVideoRefsRequest):
    """Submit reference-to-video generation using Veo or Gemini Omni Flash.

    Existing requests default to ``model_family=veo``. Set
    ``model_family=omni_flash`` and ``duration_s`` to 4/6/8/10 to use Omni.
    Omni responses include ``flowkitPolling.workflows``; poll those workflows,
    not the operation-looking handles in the raw Flow response.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    if body.model_family == "omni_flash":
        try:
            result = await generate_omni_flash_video(
                reference_media_ids=body.reference_media_ids,
                prompt=body.prompt,
                project_id=body.project_id,
                scene_id=body.scene_id,
                duration_s=body.duration_s,
                aspect_ratio=body.aspect_ratio,
                user_paygate_tier=body.user_paygate_tier,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        result = await client.generate_video_from_references(
            **body.model_dump(exclude={"model_family", "duration_s"})
        )

    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


@router.post("/generate-video-omni")
async def generate_video_omni(body: GenerateOmniFlashVideoRequest):
    """Submit Gemini Omni Flash reference-to-video generation.

    The response includes ``flowkitPolling.workflows`` for the correct
    workflow/media polling path.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    try:
        result = await generate_omni_flash_video(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


@router.post("/upscale-video")
async def upscale_video(body: UpscaleVideoRequest):
    """Submit video upscale (returns operations for polling)."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.upscale_video(**body.model_dump())
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


@router.post("/check-status")
async def check_status(body: CheckStatusRequest):
    """Check Veo operation status or Omni workflow/media status.

    Veo: pass ``operations``.
    Omni Flash: pass ``workflows`` from submit ``flowkitPolling.workflows``.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    if body.workflows:
        try:
            return await check_omni_flash_status(
                body.workflows,
                include_encoded_video=body.include_encoded_video,
                project_id=body.project_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    if not body.operations:
        raise HTTPException(400, "Provide operations for Veo or workflows for Omni Flash")

    result = await client.check_video_status(body.operations)
    if result.get("error"):
        raise HTTPException(502, result["error"])
    if isinstance(result.get("status"), int) and result["status"] >= 400:
        raise HTTPException(result["status"], result.get("data", "Flow polling failed"))
    return result.get("data", result)


@router.post("/check-omni-status")
async def check_omni_status(body: CheckOmniStatusRequest):
    """Poll Gemini Omni Flash jobs via workflow primary media IDs."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    try:
        return await check_omni_flash_status(
            body.workflows,
            include_encoded_video=body.include_encoded_video,
            project_id=body.project_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/refresh-urls/{project_id}")
async def refresh_project_urls(project_id: str):
    """Bulk refresh all media URLs for a project via per-media get_media calls."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.refresh_project_urls(project_id)
    if result.get("error"):
        raise HTTPException(502, result["error"])
    return result


@router.get("/media/{media_id}")
async def get_media(media_id: str):
    """Get media metadata + fresh signed URL from Google Flow.

    Returns the raw response which may contain ``video.encodedVideo`` for
    workflow-backed video generations.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.get_media(media_id)
    if result.get("error"):
        raise HTTPException(502, result["error"])
    status = result.get("status", 200)
    if isinstance(status, int) and status >= 400:
        raise HTTPException(status, result.get("data", "Media not found"))
    return result.get("data", result)


@router.post("/edit-image")
async def edit_image(body: EditImageRequest):
    """Edit an existing image using IMAGE_INPUT_TYPE_BASE_IMAGE (bypasses queue)."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.edit_image(
        body.prompt, body.source_media_id, body.project_id,
        aspect_ratio=body.aspect_ratio,
        user_paygate_tier=body.user_paygate_tier,
    )
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return result.get("data", result)


class UpscaleImageRequest(BaseModel):
    media_id: str
    project_id: str = ""
    target_resolution: str = "UPSAMPLE_IMAGE_RESOLUTION_4K"  # ..._2K / ..._4K
    user_paygate_tier: str = "PAYGATE_TIER_TWO"


@router.post("/upscale-image")
async def upscale_image(body: UpscaleImageRequest):
    """Upscale an existing image to 2K/4K via /v1/flow/upsampleImage (bypasses queue).

    Returns {media_id, base64, size_bytes} — the upscaled image is delivered
    synchronously as base64 in the response (same as Flow web UI download).
    """
    import base64 as b64

    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    result = await client.upscale_image(
        body.media_id, body.project_id,
        target_resolution=body.target_resolution,
        user_paygate_tier=body.user_paygate_tier,
    )
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    data = result.get("data", result) or {}

    media = data.get("media") if isinstance(data, dict) else None
    media_id = (media or {}).get("name", "")
    encoded = data.get("encodedImage", "") if isinstance(data, dict) else ""
    if not encoded:
        raise HTTPException(502, "upsampleImage response missing encodedImage")
    try:
        raw = b64.b64decode(encoded)
    except Exception as e:
        raise HTTPException(502, f"encodedImage decode failed: {e}")

    return {
        "media_id": media_id,
        "base64": encoded,
        "size_bytes": len(raw),
        "resolution": body.target_resolution,
    }


@router.post("/test-resolve-media")
async def test_resolve_media(body: dict):
    """Debug / verification endpoint — test media url resolution."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    if body.get("action") == "reload_extension":
        import json
        if client._extension_ws:
            await client._extension_ws.send(json.dumps({"method": "reload_extension"}))
            return {"status": "reloading"}
        return {"status": "no_ws"}
    media_id = body.get("media_id", "")
    resolved_url = await client.resolve_media_url(media_id)
    return {"media_id": media_id, "resolved_url": resolved_url}


@router.get("/media/image/download")
async def download_image(url: str = "", media_id: str | None = None, name: str = "image.jpg"):
    """Proxy-download an image (local cache or Flow CDN, original resolution)."""
    from urllib.parse import urlparse
    import aiohttp
    from agent.config import OUTPUT_DIR
    from agent.services.media_cache import fetch_and_cache_media, get_cached_image_path

    safe_name = "".join(c for c in name if c.isalnum() or c in "._-") or "image.jpg"
    if not safe_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        safe_name += ".jpg"

    # 1. Check media_id in local cache
    if media_id:
        cache_file = get_cached_image_path(media_id)
        if cache_file and cache_file.exists() and cache_file.stat().st_size > 0:
            return FileResponse(
                str(cache_file),
                media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
            )

    # 2. Check local path in url (e.g. /output/_cache/... or full http://.../output/...)
    if "/output/" in url or url.startswith("output/"):
        rel_path = url.split("/output/")[-1] if "/output/" in url else url.replace("output/", "")
        local_file = OUTPUT_DIR / rel_path
        if local_file.exists() and local_file.stat().st_size > 0:
            return FileResponse(
                str(local_file),
                media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
            )

    # 3. If media_id is given, resolve and cache
    if media_id:
        cached_path = await fetch_and_cache_media(media_id, url)
        if cached_path and cached_path.exists():
            return FileResponse(
                str(cached_path),
                media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
            )

    # 4. Fallback to direct HTTP fetch
    if url:
        host = (urlparse(url).hostname or "").lower()
        if (
            host.endswith("google.com")
            or host.endswith("googleusercontent.com")
            or host.endswith("ggpht.com")
            or host.endswith("flow-content.google")
            or host.endswith("storage.googleapis.com")
            or host in ("127.0.0.1", "localhost")
        ):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        media_type = resp.headers.get("content-type", "image/jpeg")
                        return Response(
                            content=data,
                            media_type=media_type,
                            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                        )

    raise HTTPException(404, "Image not found or download failed")


class UploadImageDataRequest(BaseModel):
    data_url: str  # data URL (data:image/png;base64,...) or raw base64
    project_id: str = ""
    file_name: str = "image.png"


@router.post("/upload-image-data")
async def upload_image_data(body: UploadImageDataRequest):
    """Upload an image (from browser clipboard/file) to Flow and get a media_id."""
    import base64 as b64
    import mimetypes

    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    data_url = body.data_url.strip()
    if data_url.startswith("data:"):
        meta, _, b64part = data_url.partition(",")
        mime = meta.split(";")[0].replace("data:", "") or "image/png"
    else:
        mime = mimetypes.guess_type(body.file_name)[0] or "image/png"
        b64part = data_url

    # Validate it's decodable base64 before sending to Flow
    try:
        b64.b64decode(b64part, validate=False)
    except Exception as e:
        raise HTTPException(400, f"base64 解码失败: {e}")

    result = await client.upload_image(
        b64part, mime_type=mime, project_id=body.project_id, file_name=body.file_name
    )
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    return {"media_id": result.get("_mediaId"), "raw": result.get("data", result)}


@router.post("/upload-image")
async def upload_image(body: UploadImageRequest):
    """Upload a local image file to Google Flow and get a media_id."""
    import base64, mimetypes
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    try:
        with open(body.file_path, "rb") as f:
            image_bytes = f.read()
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {body.file_path}")
    b64 = base64.b64encode(image_bytes).decode()
    mime = mimetypes.guess_type(body.file_path)[0] or "image/png"
    result = await client.upload_image(b64, mime_type=mime, project_id=body.project_id, file_name=body.file_name)
    if result.get("error") or (isinstance(result.get("status"), int) and result["status"] >= 400):
        raise HTTPException(result.get("status", 502), result.get("error", result.get("data")))
    media_id = result.get("_mediaId")
    return {"media_id": media_id, "raw": result.get("data", result)}


class ResolveMediaBatchRequest(BaseModel):
    media_ids: list[str]


@router.post("/media/resolve")
async def resolve_media_batch(body: ResolveMediaBatchRequest):
    """Batch resolve mediaKeys to fresh signed CDN URLs."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    resolved = await client.resolve_media_urls(body.media_ids)
    return {"resolved": resolved}


@router.get("/media/proxy")
async def proxy_media_image(media_id: Optional[str] = None, url: Optional[str] = None):
    """Serve cached image locally, or fetch, cache and serve on demand."""
    from fastapi.responses import FileResponse
    from agent.services.media_cache import get_cached_image_path, cache_media_image

    if media_id:
        cached_p = get_cached_image_path(media_id)
        if cached_p:
            return FileResponse(str(cached_p), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # Fetch and cache
    if media_id or url:
        cached_p = await cache_media_image(media_id or "", url)
        if cached_p and cached_p.exists():
            return FileResponse(str(cached_p), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    raise HTTPException(404, "Media not found or unable to fetch")

