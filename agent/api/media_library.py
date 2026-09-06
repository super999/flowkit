"""Unified Media Library API — single source of truth for Flow and local media."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from agent.db import crud
from agent.services import media_sync

router = APIRouter(prefix="/media-library", tags=["media-library"])


class SyncMediaRequest(BaseModel):
    project_id: Optional[str] = None
    auto_cache: bool = True


class CacheAllRequest(BaseModel):
    project_id: Optional[str] = None
    max_concurrency: int = 5


class MediaItemUpload(BaseModel):
    media_id: str
    name: str = ""
    thumb: str = ""
    project_id: str = ""
    model_name: Optional[str] = None
    aspect_ratio: Optional[str] = None


@router.get("")
async def list_media(
    project_id: Optional[str] = Query(None, description="Filter by Flow project ID"),
    is_cached: Optional[int] = Query(None, description="Filter by cache status: 1=cached, 0=uncached"),
    media_type: Optional[str] = Query(None, description="Filter by media type: image or video"),
    search: Optional[str] = Query(None, description="Search term across name, prompt, and project title"),
    sort_by: str = Query("created_at", description="Sort field: created_at, updated_at, name, project_title"),
    order: str = Query("desc", description="Sort order: desc or asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
):
    """List media from local database with pagination, sorting, and rich filters."""
    offset = (page - 1) * page_size
    items, total = await crud.list_media_library(
        project_id=project_id,
        is_cached=is_cached,
        media_type=media_type,
        search=search,
        sort_by=sort_by,
        order=order,
        limit=page_size,
        offset=offset,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/stats")
async def get_stats():
    """Get media library statistics (total, cached count, uncached count, project count)."""
    return await crud.get_media_library_stats()


@router.post("/sync")
async def trigger_sync(body: SyncMediaRequest = SyncMediaRequest()):
    """Trigger incremental synchronization from Flow projects into local database."""
    res = await media_sync.sync_flow_media(
        project_id=body.project_id,
        auto_cache=body.auto_cache,
    )
    if res.get("status") == "error":
        raise HTTPException(503, res.get("message", "Flow 扩展未连接或未登录"))
    return res


@router.post("/cache/{media_id}")
async def cache_single(media_id: str):
    """Download and persist a single media item to local disk cache."""
    item = await crud.get_media_library_item(media_id)
    url = item.get("url") if item else None
    local_path = await media_sync.cache_media_item(media_id, url=url, force=True)
    if not local_path:
        raise HTTPException(500, f"Failed to cache media {media_id}")
    return {"status": "success", "media_id": media_id, "local_path": local_path}


@router.post("/cache-all")
async def cache_all(body: CacheAllRequest = CacheAllRequest()):
    """Batch download and cache all uncached media to local disk."""
    res = await media_sync.cache_all_uncached(
        project_id=body.project_id,
        max_concurrency=body.max_concurrency,
    )
    return res


@router.post("/upload")
async def upload_local_item(body: MediaItemUpload):
    """Save an uploaded or pasted reference image into the unified media library."""
    if not body.media_id:
        raise HTTPException(400, "media_id is required")

    item = await crud.upsert_media_library_item(
        media_id=body.media_id,
        project_id=body.project_id,
        name=body.name or "本地参考图",
        prompt=body.name or "本地参考图",
        thumb=body.thumb,
        model_name=body.model_name or "GEM_PIX_2",
        aspect_ratio=body.aspect_ratio or "IMAGE_ASPECT_RATIO_PORTRAIT",
        media_type="image",
        source="upload",
        is_cached=1 if body.thumb.startswith("data:") else 0,
    )
    return item


class RepairPromptsRequest(BaseModel):
    project_id: Optional[str] = None


@router.post("/fix-prompts")
@router.post("/repair-prompts")
async def fix_prompts(body: Optional[RepairPromptsRequest] = None):
    """Restore original user prompts from Flow structured prompts, refgen_result, scenes, and clean translations."""
    pid = body.project_id if body else None
    return await media_sync.repair_flow_prompts(project_id=pid)


@router.delete("/{media_id}")
async def delete_item(media_id: str):
    """Delete a media item from the local media library database."""
    success = await crud.delete_media_library_item(media_id)
    if not success:
        raise HTTPException(404, "Media not found")
    return {"status": "success", "media_id": media_id}
