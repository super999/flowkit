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
    return await crud.list_refgen_results(project_id)


@router.post("/results")
async def create_result(body: RefGenCreate):
    return await crud.create_refgen_result(
        project_id=body.project_id,
        media_id=body.media_id,
        url=body.url,
        prompt=body.prompt,
        aspect=body.aspect,
        model=body.model,
        duration_ms=body.duration_ms,
    )


@router.delete("/results/{rid}")
async def delete_result(rid: str):
    if not await crud.delete_refgen_result(rid):
        raise HTTPException(404, "Result not found")
    return {"ok": True}
