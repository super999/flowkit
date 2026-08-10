from fastapi import APIRouter, HTTPException
from agent.models.character import Character, CharacterCreate, CharacterUpdate
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.utils.slugify import slugify

router = APIRouter(prefix="/characters", tags=["characters"])


def _get_repo() -> SQLiteRepository:
    return SQLiteRepository()


@router.post("", response_model=Character)
async def create(body: CharacterCreate):
    repo = _get_repo()
    char_data = body.model_dump(exclude_none=True)
    project_id = char_data.pop("project_id", None)
    material_id = char_data.pop("material", None)
    if "type" in char_data and "entity_type" not in char_data:
        char_data["entity_type"] = char_data.pop("type")

    # Bake a material style into the reference image prompt (unless the user
    # supplied their own image_prompt or chose "none" = write style themselves)
    if material_id and material_id != "none" and not char_data.get("image_prompt"):
        from agent.api.projects import _build_character_profile
        profile = _build_character_profile(
            body.name,
            body.description,
            None,
            entity_type=char_data.get("entity_type", "character"),
            material_id=material_id,
        )
        char_data["description"] = profile["description"]
        char_data["image_prompt"] = profile["image_prompt"]

    char = await repo.create_character(**char_data)
    if project_id:
        await repo.link_character_to_project(project_id, char.id)
    return char


@router.get("", response_model=list[Character])
async def list_all():
    repo = _get_repo()
    rows = await repo.list("character", order_by="created_at DESC")
    return [repo._row_to_character(r) for r in rows]


@router.get("/{cid}", response_model=Character)
async def get(cid: str):
    repo = _get_repo()
    c = await repo.get_character(cid)
    if not c:
        raise HTTPException(404, "Character not found")
    return c


@router.patch("/{cid}", response_model=Character)
async def update(cid: str, body: CharacterUpdate):
    repo = _get_repo()
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        updates["slug"] = slugify(updates["name"])
    row = await repo.update("character", cid, **updates)
    if not row:
        raise HTTPException(404, "Character not found")
    return repo._row_to_character(row)


@router.delete("/{cid}")
async def delete(cid: str):
    repo = _get_repo()
    if not await repo.delete_character(cid):
        raise HTTPException(404, "Character not found")
    return {"ok": True}
