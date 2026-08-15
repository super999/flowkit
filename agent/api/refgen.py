"""RefGen results API — persist reference-to-image generation outputs."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agent.db import crud

router = APIRouter(prefix="/refgen", tags=["refgen"])


class RefGenCreate(BaseModel):
    project_id: str
    media_id: str
    url: str
    prompt: str
    aspect: Optional[str] = None
    model: Optional[str] = None
    duration_ms: Optional[int] = None


@router.get("/results")
async def list_results(project_id: str):
    from agent.services.media_cache import get_cached_image_url, trigger_background_cache
    rows = await crud.list_refgen_results(project_id)
    for r in rows:
        cached_url = get_cached_image_url(r.get("media_id", ""))
        if cached_url:
            r["url"] = cached_url
        else:
            trigger_background_cache(r.get("media_id", ""), r.get("url", ""))
    return rows


@router.post("/results")
async def create_result(body: RefGenCreate):
    import asyncio
    from datetime import datetime
    from agent.services.media_cache import trigger_background_cache
    from agent.services.media_sync import cache_media_item

    # 1. Persist to refgen_results
    res = await crud.create_refgen_result(
        project_id=body.project_id,
        media_id=body.media_id,
        url=body.url,
        prompt=body.prompt,
        aspect=body.aspect,
        model=body.model,
        duration_ms=body.duration_ms,
    )

    # 2. Immediately upsert to unified media_library so it is instantly visible in Reference Library
    try:
        project = await crud.get_project(body.project_id)
        p_title = (project.get("name") or project.get("title") or "") if project else ""
        await crud.upsert_media_library_item(
            media_id=body.media_id,
            project_id=body.project_id,
            project_title=p_title,
            name=body.prompt[:50] if body.prompt else f"参考生图 ({body.media_id[:6]})",
            prompt=body.prompt,
            model_name=body.model,
            aspect_ratio=body.aspect,
            media_type="IMAGE",
            url=body.url,
            thumb=body.url,
            source="refgen",
            created_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception as e:
        pass

    # 3. Trigger immediate local cache download in background
    trigger_background_cache(body.media_id, body.url)
    asyncio.create_task(cache_media_item(body.media_id, body.url, media_type="IMAGE"))

    return res


@router.delete("/results/{rid}")
async def delete_result(rid: str):
    if not await crud.delete_refgen_result(rid):
        raise HTTPException(404, "Result not found")
    return {"ok": True}
