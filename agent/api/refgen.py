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
    from agent.services.media_cache import trigger_background_cache
    res = await crud.create_refgen_result(
        project_id=body.project_id,
        media_id=body.media_id,
        url=body.url,
        prompt=body.prompt,
        aspect=body.aspect,
        model=body.model,
        duration_ms=body.duration_ms,
    )
    trigger_background_cache(body.media_id, body.url)
    return res


@router.delete("/results/{rid}")
async def delete_result(rid: str):
    if not await crud.delete_refgen_result(rid):
        raise HTTPException(404, "Result not found")
    return {"ok": True}
