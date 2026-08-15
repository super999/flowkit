"""Async CRUD operations with column whitelisting."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from agent.db.schema import get_db, _db_lock

logger = logging.getLogger(__name__)

_VALID_TABLES = frozenset({"character", "project", "video", "scene", "request", "material", "refgen_result", "ref_image", "flow_media", "media_library"})


def _validate_table(table: str) -> None:
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table!r}")

# Column whitelists per table — prevents SQL injection via kwargs keys
_COLUMNS = {
    "character": {"name", "slug", "entity_type", "description", "image_prompt", "voice_description", "reference_image_url", "media_id", "updated_at"},
    "project": {"name", "description", "story", "thumbnail_url", "language", "status", "user_paygate_tier", "narrator_voice", "narrator_ref_audio", "material", "allow_music", "allow_voice", "updated_at"},
    "video": {"title", "description", "display_order", "status", "orientation", "vertical_url", "horizontal_url",
              "thumbnail_url", "duration", "resolution", "youtube_id", "privacy", "tags", "updated_at"},
    "scene": {"prompt", "image_prompt", "video_prompt", "character_names", "parent_scene_id", "chain_type",
              "vertical_image_url", "vertical_image_media_id", "vertical_image_status",
              "vertical_video_url", "vertical_video_media_id", "vertical_video_status",
              "vertical_upscale_url", "vertical_upscale_media_id", "vertical_upscale_status",
              "horizontal_image_url", "horizontal_image_media_id", "horizontal_image_status",
              "horizontal_video_url", "horizontal_video_media_id", "horizontal_video_status",
              "horizontal_upscale_url", "horizontal_upscale_media_id", "horizontal_upscale_status",
              "vertical_end_scene_media_id", "horizontal_end_scene_media_id",
              "trim_start", "trim_end", "duration", "display_order", "source", "transition_prompt", "narrator_text", "updated_at"},
    "request": {"status", "request_id", "media_id", "output_url", "error_message", "retry_count", "next_retry_at", "source_media_id", "updated_at"},
    "media_library": {"project_id", "project_title", "name", "prompt", "model_name", "aspect_ratio", "media_type", "url", "thumb", "local_path", "is_cached", "source", "updated_at"},
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uuid() -> str:
    return str(uuid.uuid4())


def _safe_kwargs(table: str, kwargs: dict) -> dict:
    """Filter kwargs to only allowed columns."""
    allowed = _COLUMNS.get(table, set())
    return {k: v for k, v in kwargs.items() if k in allowed}


async def _update(table: str, pk: str, pk_val: str, **kwargs) -> Optional[dict]:
    _validate_table(table)
    kwargs = _safe_kwargs(table, kwargs)
    if not kwargs:
        return await _get(table, pk, pk_val)
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [pk_val]
    db = await get_db()
    async with _db_lock:
        await db.execute(f"UPDATE {table} SET {sets} WHERE {pk}=?", vals)
        await db.commit()
    return await _get_with_db(db, table, pk, pk_val)


async def _get(table: str, pk: str, pk_val: str) -> Optional[dict]:
    _validate_table(table)
    db = await get_db()
    return await _get_with_db(db, table, pk, pk_val)


async def _get_with_db(db, table: str, pk: str, pk_val: str) -> Optional[dict]:
    _validate_table(table)
    cur = await db.execute(f"SELECT * FROM {table} WHERE {pk}=?", (pk_val,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _delete(table: str, pk: str, pk_val: str) -> bool:
    _validate_table(table)
    db = await get_db()
    async with _db_lock:
        cur = await db.execute(f"DELETE FROM {table} WHERE {pk}=?", (pk_val,))
        await db.commit()
    return cur.rowcount > 0


# ─── Character ──────────────────────────────────────────────

async def create_character(name: str, entity_type: str = "character", description: str = None, image_prompt: str = None, voice_description: str = None, reference_image_url: str = None, media_id: str = None, slug: str = None, image_model: str = None) -> dict:
    from agent.utils.slugify import slugify
    db = await get_db()
    cid, now = _uuid(), _now()
    _slug = slug or slugify(name)
    async with _db_lock:
        await db.execute(
            "INSERT INTO character (id,name,slug,entity_type,description,image_prompt,voice_description,reference_image_url,media_id,image_model,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, _slug, entity_type, description, image_prompt, voice_description, reference_image_url, media_id, image_model, now, now))
        await db.commit()
    return await _get_with_db(db, "character", "id", cid)

async def get_character(cid: str): return await _get("character", "id", cid)
async def update_character(cid: str, **kw): return await _update("character", "id", cid, **kw)
async def delete_character(cid: str): return await _delete("character", "id", cid)

async def list_characters() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM character ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


# ─── Project ────────────────────────────────────────────────

async def create_project(name: str, description: str = None, story: str = None, language: str = "en", user_paygate_tier: str = "PAYGATE_TIER_ONE", id: str = None, material: str = None, allow_music: bool = False, allow_voice: bool = False) -> dict:
    db = await get_db()
    pid, now = id or _uuid(), _now()
    async with _db_lock:
        await db.execute(
            "INSERT INTO project (id,name,description,story,language,user_paygate_tier,material,allow_music,allow_voice,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, name, description, story, language, user_paygate_tier, material, int(allow_music), int(allow_voice), now, now))
        await db.commit()
    return await _get_with_db(db, "project", "id", pid)

async def get_project(pid: str): return await _get("project", "id", pid)
async def update_project(pid: str, **kw): return await _update("project", "id", pid, **kw)
async def delete_project(pid: str): return await _delete("project", "id", pid)

async def list_projects(status: str = None) -> list[dict]:
    db = await get_db()
    if status:
        cur = await db.execute("SELECT * FROM project WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        cur = await db.execute("SELECT * FROM project ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]

async def link_character_to_project(project_id: str, character_id: str) -> bool:
    db = await get_db()
    try:
        async with _db_lock:
            await db.execute("INSERT OR IGNORE INTO project_character VALUES (?,?)", (project_id, character_id))
            await db.commit()
        return True
    except Exception as e:
        logger.warning("link_character_to_project failed: %s", e)
        return False

async def unlink_character_from_project(project_id: str, character_id: str) -> bool:
    db = await get_db()
    async with _db_lock:
        cur = await db.execute("DELETE FROM project_character WHERE project_id=? AND character_id=?", (project_id, character_id))
        await db.commit()
    return cur.rowcount > 0

async def get_project_characters(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT c.* FROM character c JOIN project_character pc ON c.id=pc.character_id WHERE pc.project_id=?",
        (project_id,))
    return [dict(r) for r in await cur.fetchall()]


# ─── Video ──────────────────────────────────────────────────

async def create_video(project_id: str, title: str, description: str = None, display_order: int = 0, orientation: str = None) -> dict:
    db = await get_db()
    vid, now = _uuid(), _now()
    async with _db_lock:
        await db.execute(
            "INSERT INTO video (id,project_id,title,description,display_order,orientation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (vid, project_id, title, description, display_order, orientation, now, now))
        await db.commit()
    return await _get_with_db(db, "video", "id", vid)

async def get_video(vid: str): return await _get("video", "id", vid)
async def update_video(vid: str, **kw): return await _update("video", "id", vid, **kw)
async def delete_video(vid: str): return await _delete("video", "id", vid)

async def list_videos(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM video WHERE project_id=? ORDER BY display_order", (project_id,))
    return [dict(r) for r in await cur.fetchall()]


# ─── Scene ──────────────────────────────────────────────────

async def create_scene(video_id: str, display_order: int, prompt: str,
                       image_prompt: str = None, video_prompt: str = None,
                       transition_prompt: str = None,
                       character_names: list[str] = None,
                       parent_scene_id: str = None, chain_type: str = "ROOT",
                       source: str = "root", image_model: str = None) -> dict:
    db = await get_db()
    sid, now = _uuid(), _now()
    chars_json = json.dumps(character_names) if character_names else None
    async with _db_lock:
        await db.execute(
            """INSERT INTO scene (id,video_id,display_order,prompt,image_prompt,video_prompt,transition_prompt,character_names,
               parent_scene_id,chain_type,source,image_model,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, video_id, display_order, prompt, image_prompt, video_prompt, transition_prompt, chars_json,
             parent_scene_id, chain_type, source, image_model, now, now))
        await db.commit()
    return await _get_with_db(db, "scene", "id", sid)

async def get_scene(sid: str): return await _get("scene", "id", sid)
async def update_scene(sid: str, **kw): return await _update("scene", "id", sid, **kw)
async def delete_scene(sid: str): return await _delete("scene", "id", sid)

async def list_scenes(video_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM scene WHERE video_id=? ORDER BY display_order", (video_id,))
    return [dict(r) for r in await cur.fetchall()]


async def list_scenes_by_media_id(media_id: str) -> list[dict]:
    """Find scenes where any media_id field matches the given UUID."""
    db = await get_db()
    cur = await db.execute(
        """SELECT * FROM scene WHERE
           vertical_image_media_id=? OR horizontal_image_media_id=?
           OR vertical_video_media_id=? OR horizontal_video_media_id=?
           OR vertical_upscale_media_id=? OR horizontal_upscale_media_id=?""",
        (media_id, media_id, media_id, media_id, media_id, media_id))
    return [dict(r) for r in await cur.fetchall()]


async def list_characters_by_media_id(media_id: str) -> list[dict]:
    """Find characters where media_id matches."""
    db = await get_db()
    cur = await db.execute("SELECT * FROM character WHERE media_id=?", (media_id,))
    return [dict(r) for r in await cur.fetchall()]


# ─── Request ────────────────────────────────────────────────

async def create_request(req_type: str, orientation: str = None,
                         scene_id: str = None, character_id: str = None,
                         project_id: str = None, video_id: str = None,
                         source_media_id: str = None, **_kw) -> dict:
    db = await get_db()
    rid, now = _uuid(), _now()
    async with _db_lock:
        await db.execute(
            """INSERT INTO request (id,project_id,video_id,scene_id,character_id,type,orientation,source_media_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, project_id, video_id, scene_id, character_id, req_type, orientation, source_media_id, now, now))
        await db.commit()
    return await _get_with_db(db, "request", "id", rid)

async def get_request(rid: str): return await _get("request", "id", rid)
async def update_request(rid: str, **kw): return await _update("request", "id", rid, **kw)

async def list_requests(scene_id: str = None, status: str = None,
                        video_id: str = None, project_id: str = None) -> list[dict]:
    db = await get_db()
    q, params = "SELECT * FROM request WHERE 1=1", []
    if scene_id:
        q += " AND scene_id=?"; params.append(scene_id)
    if status:
        q += " AND status=?"; params.append(status)
    if video_id:
        q += " AND video_id=?"; params.append(video_id)
    if project_id:
        q += " AND project_id=?"; params.append(project_id)
    q += " ORDER BY created_at DESC"
    cur = await db.execute(q, params)
    return [dict(r) for r in await cur.fetchall()]

async def list_pending_requests() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM request WHERE status='PENDING' ORDER BY created_at")
    return [dict(r) for r in await cur.fetchall()]


async def list_actionable_requests(exclude_ids: set[str] = None, limit: int = 5) -> list[dict]:
    """Priority-ordered fetch of PENDING requests ready to process."""
    db = await get_db()
    now = _now()
    exclude = exclude_ids or set()

    # Fetch all pending, filter in Python (SQLite doesn't support parameterized IN with variable length)
    cur = await db.execute("""
        SELECT * FROM request
        WHERE status = 'PENDING'
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY
          CASE type
            WHEN 'GENERATE_CHARACTER_IMAGE' THEN 0
            WHEN 'REGENERATE_CHARACTER_IMAGE' THEN 0
            WHEN 'EDIT_CHARACTER_IMAGE' THEN 0
            WHEN 'GENERATE_IMAGE' THEN 1
            WHEN 'REGENERATE_IMAGE' THEN 1
            WHEN 'EDIT_IMAGE' THEN 1
            WHEN 'GENERATE_VIDEO' THEN 2
            WHEN 'GENERATE_VIDEO_REFS' THEN 2
            WHEN 'UPSCALE_VIDEO' THEN 3
            ELSE 2
          END,
          created_at ASC
    """, (now,))
    rows = [dict(r) for r in await cur.fetchall()]
    # Exclude in-flight IDs
    filtered = [r for r in rows if r["id"] not in exclude]
    return filtered[:limit]


async def reset_stale_processing(cutoff_minutes: int = 10) -> int:
    """Reset PROCESSING requests older than cutoff back to PENDING."""
    db = await get_db()
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cutoff_minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
    async with _db_lock:
        cursor = await db.execute(
            "UPDATE request SET status='PENDING', error_message='reset: stale processing' WHERE status='PROCESSING' AND updated_at < ?",
            (cutoff,))
        await db.commit()
        return cursor.rowcount


# ─── Material ────────────────────────────────────────────────

async def create_refgen_result(project_id: str, media_id: str, url: str, prompt: str,
                               aspect: str = None, model: str = None,
                               duration_ms: int = None, id: str = None) -> dict:
    db = await get_db()
    rid, now = id or _uuid(), _now()
    async with _db_lock:
        await db.execute(
            "INSERT INTO refgen_result (id,project_id,media_id,url,prompt,aspect,model,duration_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, project_id, media_id, url, prompt, aspect, model, duration_ms, now))
        await db.commit()
    return await _get_with_db(db, "refgen_result", "id", rid)


async def list_refgen_results(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM refgen_result WHERE project_id=? ORDER BY created_at DESC", (project_id,))
    return [dict(r) for r in await cur.fetchall()]


async def delete_refgen_result(rid: str) -> bool:
    return await _delete("refgen_result", "id", rid)


# ─── Ref Image Library ───────────────────────────────────────

async def create_ref_image(media_id: str, name: str = "", thumb: str = "",
                           project_id: str = "", id: str = None) -> dict:
    db = await get_db()
    rid, now = id or _uuid(), _now()
    async with _db_lock:
        await db.execute(
            "INSERT INTO ref_image (id,project_id,media_id,name,thumb,created_at) VALUES (?,?,?,?,?,?)",
            (rid, project_id, media_id, name, thumb, now))
        await db.commit()
    return await _get_with_db(db, "ref_image", "id", rid)


async def list_ref_images() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM ref_image ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def delete_ref_image(rid: str) -> bool:
    return await _delete("ref_image", "id", rid)


# ─── Flow Media (passively captured project media) ──────────

async def upsert_flow_media(media_id: str, media_type: str, url: str) -> dict | None:
    db = await get_db()
    now = _now()
    async with _db_lock:
        await db.execute(
            """INSERT INTO flow_media (media_id,media_type,url,created_at,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(media_id) DO UPDATE SET url=excluded.url, media_type=excluded.media_type, updated_at=excluded.updated_at""",
            (media_id, media_type, url, now, now))
        await db.commit()
    return await _get_with_db(db, "flow_media", "media_id", media_id)


async def list_flow_media(limit: int = 500) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM flow_media ORDER BY updated_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in await cur.fetchall()]


async def create_material(id: str, name: str, style_instruction: str,
                          negative_prompt: str = None, scene_prefix: str = None,
                          lighting: str = None) -> dict:
    db = await get_db()
    now = _now()
    async with _db_lock:
        await db.execute(
            """INSERT INTO material (id,name,style_instruction,negative_prompt,scene_prefix,lighting,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (id, name, style_instruction, negative_prompt, scene_prefix,
             lighting or "Studio lighting, highly detailed", now))
        await db.commit()
    return await _get_with_db(db, "material", "id", id)

async def get_material(mid: str): return await _get("material", "id", mid)
async def delete_material(mid: str): return await _delete("material", "id", mid)
async def list_materials() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM material ORDER BY created_at")
    return [dict(r) for r in await cur.fetchall()]


# ─── Media Library (Consolidated) ───────────────────────────

async def upsert_media_library_item(
    media_id: str,
    project_id: str = None,
    project_title: str = None,
    name: str = None,
    prompt: str = None,
    translated_prompt: str = None,
    model_name: str = None,
    aspect_ratio: str = None,
    media_type: str = "image",
    url: str = None,
    thumb: str = None,
    local_path: str = None,
    is_cached: int = 0,
    source: str = "flow",
    created_at: str = None,
) -> dict | None:
    if not media_id:
        return None
    db = await get_db()
    now = _now()
    async with _db_lock:
        await db.execute(
            """INSERT INTO media_library (
                media_id, project_id, project_title, name, prompt, translated_prompt, model_name,
                aspect_ratio, media_type, url, thumb, local_path, is_cached, source,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(media_id) DO UPDATE SET
                project_id = COALESCE(excluded.project_id, media_library.project_id),
                project_title = COALESCE(excluded.project_title, media_library.project_title),
                name = CASE
                    WHEN media_library.prompt IS NOT NULL AND media_library.prompt != '' AND media_library.prompt NOT LIKE '%English translation of your image prompt%' AND (excluded.prompt LIKE '%English translation of your image prompt%' OR excluded.name LIKE '%English translation of your image prompt%')
                    THEN media_library.name
                    ELSE COALESCE(excluded.name, media_library.name)
                END,
                prompt = CASE
                    WHEN media_library.prompt IS NOT NULL AND media_library.prompt != '' AND media_library.prompt NOT LIKE '%English translation of your image prompt%' AND excluded.prompt LIKE '%English translation of your image prompt%'
                    THEN media_library.prompt
                    WHEN media_library.source IN ('refgen', 'upload', 'scene') AND excluded.source = 'flow' AND media_library.prompt IS NOT NULL AND media_library.prompt != ''
                    THEN media_library.prompt
                    ELSE COALESCE(excluded.prompt, media_library.prompt)
                END,
                translated_prompt = COALESCE(excluded.translated_prompt, media_library.translated_prompt),
                model_name = COALESCE(excluded.model_name, media_library.model_name),
                aspect_ratio = COALESCE(excluded.aspect_ratio, media_library.aspect_ratio),
                media_type = COALESCE(excluded.media_type, media_library.media_type),
                url = COALESCE(excluded.url, media_library.url),
                thumb = COALESCE(excluded.thumb, media_library.thumb),
                local_path = COALESCE(excluded.local_path, media_library.local_path),
                is_cached = MAX(excluded.is_cached, media_library.is_cached),
                source = COALESCE(media_library.source, excluded.source),
                updated_at = excluded.updated_at
            """,
            (
                media_id, project_id, project_title, name, prompt, translated_prompt, model_name,
                aspect_ratio, media_type, url, thumb, local_path, is_cached, source,
                created_at or now, now
            )
        )
        await db.commit()
    return await _get_with_db(db, "media_library", "media_id", media_id)


async def batch_upsert_media_library(items: list[dict]) -> int:
    if not items:
        return 0
    db = await get_db()
    now = _now()
    count = 0
    async with _db_lock:
        for item in items:
            mid = item.get("media_id") or item.get("mediaKey")
            if not mid:
                continue
            await db.execute(
                """INSERT INTO media_library (
                    media_id, project_id, project_title, name, prompt, translated_prompt, model_name,
                    aspect_ratio, media_type, url, thumb, local_path, is_cached, source,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(media_id) DO UPDATE SET
                    project_id = COALESCE(excluded.project_id, media_library.project_id),
                    project_title = COALESCE(excluded.project_title, media_library.project_title),
                    name = CASE
                        WHEN media_library.prompt IS NOT NULL AND media_library.prompt != '' AND media_library.prompt NOT LIKE '%English translation of your image prompt%' AND (excluded.prompt LIKE '%English translation of your image prompt%' OR excluded.name LIKE '%English translation of your image prompt%')
                        THEN media_library.name
                        ELSE COALESCE(excluded.name, media_library.name)
                    END,
                    prompt = CASE
                        WHEN media_library.prompt IS NOT NULL AND media_library.prompt != '' AND media_library.prompt NOT LIKE '%English translation of your image prompt%' AND excluded.prompt LIKE '%English translation of your image prompt%'
                        THEN media_library.prompt
                        WHEN media_library.source IN ('refgen', 'upload', 'scene') AND excluded.source = 'flow' AND media_library.prompt IS NOT NULL AND media_library.prompt != ''
                        THEN media_library.prompt
                        ELSE COALESCE(excluded.prompt, media_library.prompt)
                    END,
                    translated_prompt = COALESCE(excluded.translated_prompt, media_library.translated_prompt),
                    model_name = COALESCE(excluded.model_name, media_library.model_name),
                    aspect_ratio = COALESCE(excluded.aspect_ratio, media_library.aspect_ratio),
                    media_type = COALESCE(excluded.media_type, media_library.media_type),
                    url = COALESCE(excluded.url, media_library.url),
                    thumb = COALESCE(excluded.thumb, media_library.thumb),
                    local_path = COALESCE(excluded.local_path, media_library.local_path),
                    is_cached = MAX(excluded.is_cached, media_library.is_cached),
                    source = COALESCE(media_library.source, excluded.source),
                    updated_at = excluded.updated_at
                """,
                (
                    mid,
                    item.get("project_id"),
                    item.get("project_title"),
                    item.get("name"),
                    item.get("prompt"),
                    item.get("translated_prompt"),
                    item.get("model_name") or item.get("modelName"),
                    item.get("aspect_ratio") or item.get("aspectRatio"),
                    item.get("media_type") or item.get("mediaType") or "image",
                    item.get("url"),
                    item.get("thumb"),
                    item.get("local_path"),
                    int(bool(item.get("is_cached"))),
                    item.get("source") or "flow",
                    item.get("created_at") or item.get("createTime") or now,
                    now
                )
            )
            count += 1
        await db.commit()
    return count


async def list_media_library(
    project_id: str = None,
    is_cached: int = None,
    media_type: str = None,
    search: str = None,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 60,
    offset: int = 0,
) -> tuple[list[dict], int]:
    db = await get_db()
    conditions = []
    params = []

    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if is_cached is not None:
        conditions.append("is_cached = ?")
        params.append(int(is_cached))
    if media_type:
        conditions.append("LOWER(media_type) = LOWER(?)")
        params.append(media_type)
    if search:
        conditions.append("(name LIKE ? OR prompt LIKE ? OR translated_prompt LIKE ? OR project_title LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term, term])

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Count total
    count_cur = await db.execute(f"SELECT COUNT(*) FROM media_library {where_clause}", tuple(params))
    total = (await count_cur.fetchone())[0]

    # Determine sorting
    sort_col = "created_at"
    if sort_by in ("created_at", "updated_at", "name", "project_title", "media_id"):
        sort_col = sort_by

    sort_dir = "ASC" if (order or "").lower() == "asc" else "DESC"
    order_clause = f"ORDER BY {sort_col} {sort_dir}, updated_at DESC"

    # Select items
    query = f"SELECT * FROM media_library {where_clause} {order_clause} LIMIT ? OFFSET ?"
    item_params = (*params, limit, offset)
    cur = await db.execute(query, item_params)
    rows = [dict(r) for r in await cur.fetchall()]

    return rows, total


async def get_media_library_item(media_id: str) -> dict | None:
    return await _get("media_library", "media_id", media_id)


async def update_media_library_item(media_id: str, **kw) -> dict | None:
    return await _update("media_library", "media_id", media_id, **kw)


async def delete_media_library_item(media_id: str) -> bool:
    return await _delete("media_library", "media_id", media_id)


async def get_media_library_stats() -> dict:
    db = await get_db()
    cur = await db.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_cached = 1 THEN 1 ELSE 0 END) as cached_count,
            SUM(CASE WHEN is_cached = 0 THEN 1 ELSE 0 END) as uncached_count,
            COUNT(DISTINCT project_id) as project_count
        FROM media_library
    """)
    row = await cur.fetchone()
    if row:
        return {
            "total": row[0] or 0,
            "cached_count": row[1] or 0,
            "uncached_count": row[2] or 0,
            "project_count": row[3] or 0,
        }
    return {"total": 0, "cached_count": 0, "uncached_count": 0, "project_count": 0}

