"""Reference image library API — persistent reuse of uploaded reference images."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agent.db import crud

router = APIRouter(prefix="/ref-images", tags=["ref-images"])


class RefImageCreate(BaseModel):
    media_id: str
    name: str = ""
    thumb: str = ""
    project_id: str = ""


@router.get("")
async def list_images():
    return await crud.list_ref_images()


@router.post("")
async def create_image(body: RefImageCreate):
    if not body.media_id:
        raise HTTPException(400, "media_id required")
    return await crud.create_ref_image(
        media_id=body.media_id,
        name=body.name,
        thumb=body.thumb,
        project_id=body.project_id,
    )


@router.delete("/{rid}")
async def delete_image(rid: str):
    if not await crud.delete_ref_image(rid):
        raise HTTPException(404, "Image not found")
    return {"ok": True}
