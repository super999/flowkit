"""Flow Media Synchronization & Local Persistence Service.

Provides:
- Incremental sync from Google Flow projects into the local `media_library` database table.
- Automatic background disk caching of images into `output/_cache/<media_id>.jpg`.
- One-click manual caching & batch caching.
- Auto-healing of expired 403 Google CDN URLs.
- Background periodic sync runner.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from agent.config import MEDIA_CACHE_DIR
from agent.db import crud
from agent.services.flow_client import get_flow_client
from agent.services.media_cache import get_cached_image_path, download_to_file

logger = logging.getLogger("flowkit.media_sync")

# Global background tasks reference
_sync_task: Optional[asyncio.Task] = None
_caching_task: Optional[asyncio.Task] = None
_is_syncing = False
_is_caching = False


async def cache_media_item(
    media_id: str,
    url: Optional[str] = None,
    force: bool = False,
    media_type: str = "IMAGE",
) -> Optional[str]:
    """Download and persist a single media item to local disk.
    
    If URL is expired or missing, dynamically resolves a fresh signed URL via FlowClient.
    Updates `media_library` record upon success.
    Returns `/output/_cache/{media_id}.(jpg|mp4)` on success.
    """
    if not media_id:
        return None

    is_video = (media_type or "").upper() == "VIDEO"
    ext = "mp4" if is_video else "jpg"
    dest_path = MEDIA_CACHE_DIR / f"{media_id}.{ext}"
    local_rel = f"/output/_cache/{media_id}.{ext}"

    if not force and dest_path.exists() and dest_path.stat().st_size > 0:
        await crud.update_media_library_item(media_id, local_path=local_rel, is_cached=1)
        return local_rel

    # 1. Try provided URL if available
    downloaded = False
    if url and url.startswith("http") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        downloaded = await download_to_file(url, dest_path)

    # 2. If URL failed (e.g. 403 expired) or missing, resolve fresh signed URL from Flow
    if not downloaded:
        client = get_flow_client()
        if client and client.connected:
            try:
                fresh_url = await client.resolve_media_url(media_id, timeout=15)
                if fresh_url:
                    downloaded = await download_to_file(fresh_url, dest_path)
                    if downloaded:
                        # Also update the URL column in DB with the fresh URL
                        await crud.update_media_library_item(media_id, url=fresh_url)
            except Exception as e:
                logger.debug("Failed to resolve fresh URL for %s: %s", media_id, e)

    if downloaded and dest_path.exists() and dest_path.stat().st_size > 0:
        await crud.update_media_library_item(media_id, local_path=local_rel, is_cached=1)
        logger.info("Successfully persisted media %s (%s) to local cache", media_id, ext)
        return local_rel

    return None


def _clean_flow_prompt(p: str) -> str:
    if not p:
        return ""
    p = p.strip()
    prefix = "Here's the English translation of your image prompt:"
    if prefix.lower() in p.lower():
        idx = p.lower().find(prefix.lower())
        rest = p[idx + len(prefix):].strip()
        if (rest.startswith('"') and rest.endswith('"')) or (rest.startswith("'") and rest.endswith("'")):
            rest = rest[1:-1].strip()
        return rest
    return p


async def sync_flow_media(project_id: Optional[str] = None, auto_cache: bool = True) -> Dict[str, Any]:
    """Incrementally synchronize Flow project media into the local `media_library` table.
    
    If project_id is provided, syncs that project. Otherwise syncs all projects in the account.
    If auto_cache=True, starts a background task to download uncached items.
    """
    global _is_syncing
    if _is_syncing:
        return {"status": "already_running", "message": "增量同步正在进行中，请稍候"}

    client = get_flow_client()
    if not client or not client.connected:
        return {"status": "error", "synced": 0, "message": "Flow 扩展未连接或未登录"}

    _is_syncing = True
    total_synced = 0
    projects_synced = 0

    try:
        projects_to_sync: List[Dict[str, Any]] = []

        if project_id:
            project = await crud.get_project(project_id)
            title = (project.get("name") or project.get("title") or "") if project else ""
            if not title:
                try:
                    all_projs = await client.search_user_projects(limit=100)
                    p_match = next((p for p in all_projs if p.get("projectId") == project_id), None)
                    title = p_match.get("projectInfo", {}).get("projectTitle", "") if p_match else ""
                except Exception:
                    pass
            projects_to_sync.append({"projectId": project_id, "title": title})
        else:
            all_projs = await client.search_user_projects(limit=100)
            for p in all_projs:
                pid = p.get("projectId")
                if pid:
                    title = p.get("projectInfo", {}).get("projectTitle", "")
                    projects_to_sync.append({"projectId": pid, "title": title})

        logger.info("Syncing media for %d Flow projects in parallel...", len(projects_to_sync))

        # Pre-load all local user prompts from refgen_result, character & scene
        local_user_prompts = {}
        try:
            db = await crud.get_db()
            cur = await db.execute("SELECT media_id, prompt FROM refgen_result WHERE prompt IS NOT NULL AND prompt != ''")
            for r in await cur.fetchall():
                local_user_prompts[r["media_id"]] = r["prompt"]
            cur_char = await db.execute("SELECT media_id, description, image_prompt, name FROM character WHERE media_id IS NOT NULL AND media_id != ''")
            for r in await cur_char.fetchall():
                local_user_prompts[r["media_id"]] = r["image_prompt"] or r["description"] or r["name"]
            cur_v = await db.execute("SELECT vertical_image_media_id, prompt FROM scene WHERE vertical_image_media_id IS NOT NULL AND prompt IS NOT NULL AND prompt != ''")
            for r in await cur_v.fetchall():
                local_user_prompts[r["vertical_image_media_id"]] = r["prompt"]
            cur_h = await db.execute("SELECT horizontal_image_media_id, prompt FROM scene WHERE horizontal_image_media_id IS NOT NULL AND prompt IS NOT NULL AND prompt != ''")
            for r in await cur_h.fetchall():
                local_user_prompts[r["horizontal_image_media_id"]] = r["prompt"]
        except Exception as e:
            logger.debug("Failed to pre-load local user prompts: %s", e)

        sem = asyncio.Semaphore(4)

        async def _sync_single_project(p_info: Dict[str, Any]) -> tuple[int, bool]:
            pid = p_info["projectId"]
            p_title = p_info["title"]
            async with sem:
                try:
                    # 8s timeout per project to prevent one dead project hanging the whole sync
                    media_items = await asyncio.wait_for(
                        client.fetch_project_media(pid, limit=150),
                        timeout=8.0
                    )
                    if not media_items:
                        return 0, False

                    batch_items = []
                    for item in media_items:
                        mid = item.get("mediaKey") or item.get("media_id")
                        if not mid:
                            continue

                        raw_mtype = item.get("mediaType") or "IMAGE"
                        media_type = "VIDEO" if raw_mtype.upper() == "VIDEO" else "IMAGE"
                        ext = "mp4" if media_type == "VIDEO" else "jpg"

                        # Check if already cached locally
                        cached_p = MEDIA_CACHE_DIR / f"{mid}.{ext}"
                        is_cached = 1 if (cached_p.exists() and cached_p.stat().st_size > 0) else 0
                        local_path = f"/output/_cache/{mid}.{ext}" if is_cached else None

                        raw_flow_prompt = item.get("prompt") or ""
                        cleaned_flow_prompt = _clean_flow_prompt(raw_flow_prompt)
                        user_orig_prompt = local_user_prompts.get(mid)

                        # prompt: original user prompt (or cleaned flow prompt)
                        # translated_prompt: underlying translated english prompt if different
                        if user_orig_prompt and user_orig_prompt != cleaned_flow_prompt:
                            prompt_text = user_orig_prompt
                            translated_prompt_text = cleaned_flow_prompt
                        else:
                            prompt_text = cleaned_flow_prompt
                            translated_prompt_text = None

                        model = item.get("modelName") or ""
                        aspect = item.get("aspectRatio") or ""
                        thumb = item.get("url") or ""

                        batch_items.append({
                            "media_id": mid,
                            "project_id": pid,
                            "project_title": p_title,
                            "name": prompt_text[:50] if prompt_text else f"Flow {media_type} ({mid[:6]})",
                            "prompt": prompt_text,
                            "translated_prompt": translated_prompt_text,
                            "model_name": model,
                            "aspect_ratio": aspect,
                            "media_type": media_type,
                            "url": thumb,
                            "thumb": thumb,
                            "local_path": local_path,
                            "is_cached": is_cached,
                            "source": "refgen" if mid in local_user_prompts else "flow",
                            "created_at": item.get("createTime") or None,
                        })

                    if batch_items:
                        upserted = await crud.batch_upsert_media_library(batch_items)
                        logger.info("Synced %d media items from project '%s' (%s)", upserted, p_title, pid[:8])
                        return upserted, True

                    return 0, True
                except asyncio.TimeoutError:
                    logger.debug("Project %s sync timed out after 8s", pid[:8])
                    return 0, False
                except Exception as e:
                    logger.warning("Failed to sync media for project %s: %s", pid[:8], e)
                    return 0, False

        sync_results = await asyncio.gather(*[_sync_single_project(p) for p in projects_to_sync])
        for upserted, success in sync_results:
            total_synced += upserted
            if success:
                projects_synced += 1

        # Trigger auto-caching in the background if requested
        if auto_cache:
            trigger_background_cache_all()

        return {
            "status": "success",
            "synced": total_synced,
            "projects_synced": projects_synced,
            "message": f"成功增量同步 {total_synced} 条媒体（来自 {projects_synced} 个项目）",
        }

    finally:
        _is_syncing = False


async def cache_all_uncached(project_id: Optional[str] = None, max_concurrency: int = 5) -> Dict[str, Any]:
    """Batch download and cache all uncached media to local disk."""
    global _is_caching
    if _is_caching:
        return {"status": "already_running", "message": "批量缓存任务正在后台进行中"}

    _is_caching = True
    try:
        # Fetch uncached media items from database
        rows, total = await crud.list_media_library(project_id=project_id, is_cached=0, limit=500)
        if not rows:
            return {"status": "success", "cached": 0, "total": 0, "message": "所有图片均已在本地持久化缓存"}

        logger.info("Starting batch caching for %d uncached media items (max_concurrency=%d)...", len(rows), max_concurrency)

        sem = asyncio.Semaphore(max_concurrency)
        success_count = 0
        fail_count = 0

        async def _worker(item: dict):
            nonlocal success_count, fail_count
            mid = item["media_id"]
            url = item.get("url")
            mtype = item.get("media_type", "IMAGE")
            async with sem:
                res = await cache_media_item(mid, url=url, media_type=mtype)
                if res:
                    success_count += 1
                else:
                    fail_count += 1
                await asyncio.sleep(0.05)

        tasks = [_worker(r) for r in rows]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Batch caching completed: %d success, %d failed", success_count, fail_count)
        return {
            "status": "success",
            "cached": success_count,
            "failed": fail_count,
            "total": len(rows),
            "message": f"缓存完成：{success_count} 张已持久化到本地，{fail_count} 张失败",
        }

    finally:
        _is_caching = False


def trigger_background_cache_all(project_id: Optional[str] = None):
    """Fire-and-forget trigger for batch caching without blocking the API response."""
    global _caching_task
    if _is_caching:
        return
    try:
        loop = asyncio.get_running_loop()
        _caching_task = loop.create_task(cache_all_uncached(project_id=project_id))
    except RuntimeError:
        pass


async def _periodic_sync_loop(interval_seconds: int = 300):
    """Background loop that periodically performs incremental sync."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            client = get_flow_client()
            if client and client.connected:
                logger.info("Periodic Flow media sync triggered...")
                await sync_flow_media(auto_cache=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Periodic sync loop error: %s", e)


def start_periodic_sync(interval_seconds: int = 300):
    """Start the periodic background sync runner."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        try:
            loop = asyncio.get_running_loop()
            _sync_task = loop.create_task(_periodic_sync_loop(interval_seconds))
            logger.info("Started periodic Flow media sync loop (every %ds)", interval_seconds)
        except RuntimeError:
            pass


def stop_periodic_sync():
    """Stop the periodic background sync runner."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        _sync_task = None
