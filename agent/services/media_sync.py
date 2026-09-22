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
from agent.services.media_cache import get_cached_image_path, download_to_file, is_valid_media_file

logger = logging.getLogger("flowkit.media_sync")

# Global background tasks reference
_sync_task: Optional[asyncio.Task] = None
_caching_task: Optional[asyncio.Task] = None
_is_syncing = False
_is_caching = False

# ``Zzl0ze`` can take more than a minute for a large Flow project.  The old
# 25-second guard cancelled that request while the extension was still
# working, which made a healthy sync look like an empty project.  Keep a
# guard so a broken project cannot block all other projects forever, but leave
# enough room for the batchexecute request and its media URL lookups.
PROJECT_SYNC_TIMEOUT_SECONDS = 180.0
# ``None`` asks FlowClient for the complete project listing.  The old 150/200
# item cap silently dropped newer media from large projects.
PROJECT_MEDIA_LIMIT: Optional[int] = None


def _text_value(value: Any) -> str:
    """Return a stripped display value, ignoring non-string/empty values."""
    if value is None:
        return ""
    value = str(value).strip()
    return value


def _project_id(project: Dict[str, Any]) -> str:
    """Read a project id from the shapes used by old and batch Flow clients."""
    if not isinstance(project, dict):
        return ""
    for key in ("projectId", "project_id", "id"):
        value = _text_value(project.get(key))
        if value:
            return value
    return ""


def _project_title(project: Dict[str, Any], fallback: str = "") -> str:
    """Read a human project title without treating a UUID as its name.

    The legacy project response nested the title under ``projectInfo`` while
    the batch/local-link response puts it in top-level ``title``.  A UUID is
    useful as an id, but storing it as ``project_title`` causes the dashboard
    to display the first eight characters instead of the real title.
    """
    if isinstance(project, dict):
        info = project.get("projectInfo")
        info = info if isinstance(info, dict) else {}
        pid = _project_id(project)
        for source in (info, project):
            for key in (
                "projectTitle",
                "title",
                "project_title",
                "projectName",
                "name",
                "displayName",
            ):
                value = _text_value(source.get(key))
                if value and value != pid:
                    return value

    return _text_value(fallback)


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

    if not force and is_valid_media_file(dest_path):
        await crud.update_media_library_item(media_id, local_path=local_rel, is_cached=1)
        return local_rel

    # 1. Try provided URL if available
    downloaded = False
    if url and url.startswith("http") and "/asb/" not in url and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        downloaded = await download_to_file(url, dest_path)

    # 2. If URL failed (e.g. 403 expired) or missing, resolve fresh signed URL from Flow
    if not downloaded:
        client = get_flow_client()
        if client and client.connected:
            try:
                fresh_url = await client.resolve_media_url(media_id, timeout=60)
                if fresh_url:
                    downloaded = await download_to_file(fresh_url, dest_path)
                    if downloaded:
                        # Also update the URL column in DB with the fresh URL
                        await crud.update_media_library_item(media_id, url=fresh_url)
            except Exception as e:
                logger.debug("Failed to resolve fresh URL for %s: %s", media_id, e)

    if downloaded and is_valid_media_file(dest_path):
        await crud.update_media_library_item(media_id, local_path=local_rel, is_cached=1)
        logger.info("Successfully persisted media %s (%s) to local cache", media_id, ext)
        return local_rel

    return None


def _clean_flow_prompt(p: str) -> str:
    if not p:
        return ""
    from agent.services.flow_client import clean_flow_prompt_text
    return clean_flow_prompt_text(p)


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
    failed_projects: List[Dict[str, str]] = []

    try:
        projects_to_sync: List[Dict[str, Any]] = []

        if project_id:
            project = await crud.get_project(project_id)
            title = _project_title(project) if project else ""
            if not title:
                try:
                    all_projs = await client.search_user_projects(limit=100)
                    p_match = next(
                        (p for p in all_projs if _project_id(p) == project_id),
                        None,
                    )
                    title = _project_title(p_match) if p_match else ""
                except Exception as e:
                    logger.warning("Failed to resolve title for Flow project %s: %s", project_id[:8], e)
            projects_to_sync.append({"projectId": project_id, "title": title})
        else:
            try:
                all_projs = await client.search_user_projects(limit=100)
            except Exception as e:
                message = f"无法获取 Flow 项目列表: {e}"
                logger.exception("Flow project discovery failed")
                return {
                    "status": "error",
                    "synced": 0,
                    "projects_synced": 0,
                    "projects_failed": 0,
                    "errors": [message],
                    "message": message,
                }
            for p in all_projs:
                pid = _project_id(p)
                if pid:
                    title = _project_title(p)
                    projects_to_sync.append({"projectId": pid, "title": title})

        logger.info("Syncing media for %d Flow projects in parallel...", len(projects_to_sync))

        # Pre-load existing media metadata as a fallback.  The Flow listing can
        # omit prompt/model/url fields for older or newly-created media; those
        # empty values must not erase useful local metadata on upsert.
        existing_media: Dict[str, Dict[str, Any]] = {}
        try:
            existing_rows, _ = await crud.list_media_library(limit=10000)
            existing_media = {
                row["media_id"]: row
                for row in existing_rows
                if isinstance(row, dict) and row.get("media_id")
            }
        except Exception as e:
            logger.debug("Failed to pre-load existing media metadata: %s", e)

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

        async def _sync_single_project(p_info: Dict[str, Any]) -> tuple[int, bool, Optional[str]]:
            pid = p_info["projectId"]
            p_title = _text_value(p_info.get("title")) or None
            async with sem:
                try:
                    # Large project listings can take 55s+ over the extension
                    # bridge.  Keep this guard well above that observed case.
                    media_items = await asyncio.wait_for(
                        client.fetch_project_media(pid, limit=PROJECT_MEDIA_LIMIT),
                        timeout=PROJECT_SYNC_TIMEOUT_SECONDS,
                    )
                    if not media_items:
                        return 0, True, None

                    batch_items = []
                    for item in media_items:
                        if not isinstance(item, dict):
                            continue
                        mid = item.get("mediaKey") or item.get("media_id")
                        if not mid:
                            continue

                        existing = existing_media.get(mid, {})
                        raw_mtype = item.get("mediaType") or item.get("media_type") or existing.get("media_type") or "IMAGE"
                        media_type = "VIDEO" if _text_value(raw_mtype).upper() == "VIDEO" else "IMAGE"
                        ext = "mp4" if media_type == "VIDEO" else "jpg"

                        # Check if already cached locally
                        cached_p = MEDIA_CACHE_DIR / f"{mid}.{ext}"
                        is_cached = 1 if is_valid_media_file(cached_p) else 0
                        local_path = f"/output/_cache/{mid}.{ext}" if is_cached else None

                        flow_orig_prompt = _clean_flow_prompt(
                            _text_value(item.get("prompt") or item.get("original_prompt"))
                        )
                        flow_trans_prompt = _clean_flow_prompt(
                            _text_value(item.get("translated_prompt") or item.get("translatedPrompt"))
                        ) or None
                        user_orig_prompt = local_user_prompts.get(mid)
                        existing_prompt = _clean_flow_prompt(_text_value(existing.get("prompt")))

                        # prompt: original user prompt (or Flow Chinese/original prompt)
                        # translated_prompt: underlying translated english prompt if different
                        if user_orig_prompt:
                            prompt_text = _text_value(user_orig_prompt)
                            translated_prompt_text = (
                                flow_trans_prompt
                                if flow_trans_prompt and flow_trans_prompt != prompt_text
                                else (flow_orig_prompt if flow_orig_prompt != prompt_text else None)
                            )
                        else:
                            prompt_text = flow_orig_prompt or existing_prompt or flow_trans_prompt or ""
                            translated_prompt_text = (
                                flow_trans_prompt
                                if flow_trans_prompt and flow_trans_prompt != prompt_text
                                else None
                            )

                        model = _text_value(item.get("modelName") or item.get("model_name") or existing.get("model_name")) or None
                        aspect = _text_value(item.get("aspectRatio") or item.get("aspect_ratio") or existing.get("aspect_ratio")) or None
                        thumb = _text_value(item.get("url") or item.get("thumb") or existing.get("url") or existing.get("thumb")) or None
                        item_title = _text_value(item.get("title") or item.get("name"))
                        project_title = p_title or _text_value(existing.get("project_title")) or None
                        prompt_value = prompt_text or None
                        name = (prompt_text[:50] if prompt_text else item_title or existing.get("name")) or None

                        batch_items.append({
                            "media_id": mid,
                            "project_id": pid,
                            "project_title": project_title,
                            "name": name,
                            "prompt": prompt_value,
                            "translated_prompt": translated_prompt_text,
                            "model_name": model,
                            "aspect_ratio": aspect,
                            "media_type": media_type,
                            "url": thumb,
                            "thumb": thumb,
                            "local_path": local_path,
                            "is_cached": is_cached,
                            "source": "refgen" if mid in local_user_prompts else "flow",
                            "created_at": item.get("createTime") or item.get("created_at") or existing.get("created_at") or None,
                        })

                    if batch_items:
                        upserted = await crud.batch_upsert_media_library(batch_items)
                        logger.info("Synced %d media items from project '%s' (%s)", upserted, p_title or pid[:8], pid[:8])
                        return upserted, True, None

                    return 0, True, None
                except asyncio.TimeoutError:
                    error = f"项目 {pid[:8]} 同步超时（>{PROJECT_SYNC_TIMEOUT_SECONDS:.0f}s）"
                    logger.warning(error)
                    return 0, False, error
                except Exception as e:
                    error = f"项目 {pid[:8]} 同步失败: {e}"
                    logger.warning(error, exc_info=True)
                    return 0, False, error

        sync_results = await asyncio.gather(*[_sync_single_project(p) for p in projects_to_sync])
        for p_info, (upserted, success, error) in zip(projects_to_sync, sync_results):
            total_synced += upserted
            if success:
                projects_synced += 1
            else:
                failed_projects.append({
                    "project_id": p_info["projectId"],
                    "project_title": p_info.get("title") or p_info["projectId"],
                    "error": error or "未知错误",
                })

        # Trigger auto-caching in the background if requested
        if auto_cache and total_synced:
            trigger_background_cache_all()

        if failed_projects and projects_synced:
            status = "partial"
            message = (
                f"部分同步完成：已同步 {total_synced} 条媒体（来自 {projects_synced} 个项目），"
                f"{len(failed_projects)} 个项目失败"
            )
        elif failed_projects:
            status = "error"
            message = f"同步失败：{len(failed_projects)} 个项目未完成；{failed_projects[0]['error']}"
        else:
            status = "success"
            message = f"成功增量同步 {total_synced} 条媒体（来自 {projects_synced} 个项目）"

        return {
            "status": status,
            "synced": total_synced,
            "projects_synced": projects_synced,
            "projects_failed": len(failed_projects),
            "errors": [item["error"] for item in failed_projects],
            "failed_projects": failed_projects,
            "message": message,
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
        # Earlier versions cached HTTP-200 login pages as images. Recheck the
        # actual files so the cache button repairs these false positives too.
        all_rows, _ = await crud.list_media_library(project_id=project_id, limit=10000)
        rows = []
        for item in all_rows:
            if item.get("is_cached"):
                ext = "mp4" if (item.get("media_type") or "").upper() == "VIDEO" else "jpg"
                cached_path = MEDIA_CACHE_DIR / f"{item['media_id']}.{ext}"
                if is_valid_media_file(cached_path):
                    continue
                await crud.update_media_library_item(item["media_id"], is_cached=0, local_path=None)
            rows.append(item)
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
            "status": "partial" if fail_count and success_count else "error" if fail_count else "success",
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


async def repair_flow_prompts(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Batch repair historical media prompts in media_library by re-reading Flow project initial data.

    1. Reconnects/queries Flow client for projects.
    2. Fetches full `projectInitialData` and extracts Chinese user prompts + English translated prompts.
    3. Restores prompts from local `refgen_result` and `scene`.
    4. Updates database records where prompt was previously saved as English or translated_prompt was missing.
    """
    client = get_flow_client()
    if not client or not client.connected:
        return {"status": "error", "repaired": 0, "message": "Flow 扩展未连接或未登录"}

    db = await crud.get_db()
    repaired_count = 0
    restored_local = 0
    cleaned_count = 0
    failed_projects: List[Dict[str, str]] = []

    # 1. Restore local user prompts from refgen_result & scene first
    local_prompts = {}
    async with crud._db_lock:
        cur1 = await db.execute("SELECT media_id, prompt FROM refgen_result WHERE prompt IS NOT NULL AND prompt != ''")
        for r in await cur1.fetchall():
            local_prompts[r["media_id"]] = r["prompt"]
            await db.execute("UPDATE media_library SET prompt = ?, name = ?, source = 'refgen' WHERE media_id = ?", (r["prompt"], r["prompt"][:50], r["media_id"]))
            restored_local += 1

        cur_v = await db.execute("SELECT vertical_image_media_id, prompt FROM scene WHERE vertical_image_media_id IS NOT NULL AND prompt IS NOT NULL AND prompt != ''")
        for r in await cur_v.fetchall():
            local_prompts[r["vertical_image_media_id"]] = r["prompt"]
            await db.execute("UPDATE media_library SET prompt = ?, name = ? WHERE media_id = ?", (r["prompt"], r["prompt"][:50], r["vertical_image_media_id"]))
            restored_local += 1

        cur_h = await db.execute("SELECT horizontal_image_media_id, prompt FROM scene WHERE horizontal_image_media_id IS NOT NULL AND prompt IS NOT NULL AND prompt != ''")
        for r in await cur_h.fetchall():
            local_prompts[r["horizontal_image_media_id"]] = r["prompt"]
            await db.execute("UPDATE media_library SET prompt = ?, name = ? WHERE media_id = ?", (r["prompt"], r["prompt"][:50], r["horizontal_image_media_id"]))
            restored_local += 1

        await db.commit()

    # 2. Get list of Flow projects to inspect
    projects_to_check = []
    if project_id:
        projects_to_check.append(project_id)
    else:
        try:
            projs = await client.search_user_projects(limit=100)
            projects_to_check = [_project_id(p) for p in projs if _project_id(p)]
        except Exception as e:
            error = f"无法获取 Flow 项目列表: {e}"
            logger.warning("Prompt repair project discovery failed: %s", e, exc_info=True)
            return {
                "status": "error",
                "repaired": 0,
                "restored_local": restored_local,
                "cleaned": cleaned_count,
                "projects_failed": 0,
                "errors": [error],
                "message": error,
            }

    # 3. For each project, fetch project media and extract prompts
    for pid in projects_to_check:
        try:
            media_items = await asyncio.wait_for(
                client.fetch_project_media(pid, limit=PROJECT_MEDIA_LIMIT),
                timeout=PROJECT_SYNC_TIMEOUT_SECONDS,
            )
            if not media_items:
                continue

            async with crud._db_lock:
                for item in media_items:
                    if not isinstance(item, dict):
                        continue
                    mid = item.get("mediaKey") or item.get("media_id")
                    if not mid:
                        continue

                    orig_p = _clean_flow_prompt(_text_value(item.get("prompt") or item.get("original_prompt")))
                    trans_p = _clean_flow_prompt(
                        _text_value(item.get("translated_prompt") or item.get("translatedPrompt"))
                    ) or None

                    if mid in local_prompts:
                        orig_p = local_prompts[mid]

                    if not orig_p and not trans_p:
                        continue

                    if orig_p:
                        await db.execute(
                            """UPDATE media_library 
                               SET prompt = ?, 
                                   name = ?, 
                                   translated_prompt = COALESCE(?, translated_prompt)
                               WHERE media_id = ?""",
                            (orig_p, orig_p[:50], trans_p, mid)
                        )
                        repaired_count += 1
                    elif trans_p:
                        await db.execute(
                            """UPDATE media_library 
                               SET translated_prompt = ?
                               WHERE media_id = ?""",
                            (trans_p, mid)
                        )
                        repaired_count += 1

                await db.commit()
        except asyncio.TimeoutError:
            error = f"项目 {pid[:8]} 提示词修复超时（>{PROJECT_SYNC_TIMEOUT_SECONDS:.0f}s）"
            failed_projects.append({"project_id": pid, "error": error})
            logger.warning(error)
        except Exception as e:
            error = f"项目 {pid[:8]} 提示词修复失败: {e}"
            failed_projects.append({"project_id": pid, "error": error})
            logger.warning(error, exc_info=True)

    # 4. Clean any residual translation preambles
    async with crud._db_lock:
        cur3 = await db.execute("SELECT media_id, prompt, translated_prompt FROM media_library WHERE prompt LIKE '%English translation of your image prompt%' OR translated_prompt LIKE '%English translation of your image prompt%'")
        for r in await cur3.fetchall():
            mid = r["media_id"]
            p = _clean_flow_prompt(r["prompt"]) if r["prompt"] else None
            tp = _clean_flow_prompt(r["translated_prompt"]) if r["translated_prompt"] else None
            await db.execute("UPDATE media_library SET prompt = COALESCE(?, prompt), name = COALESCE(?, name), translated_prompt = COALESCE(?, translated_prompt) WHERE media_id = ?", 
                             (p, p[:50] if p else None, tp, mid))
            cleaned_count += 1
        await db.commit()

    if failed_projects and (repaired_count or restored_local or cleaned_count):
        status = "partial"
        message = (
            f"部分修复完成：修复 {repaired_count} 条云端原版提示词，恢复 {restored_local} 条本地提示词，"
            f"清理 {cleaned_count} 条格式异常词；{len(failed_projects)} 个项目失败。"
        )
    elif failed_projects:
        status = "error"
        message = f"提示词修复失败：{failed_projects[0]['error']}"
    else:
        status = "success"
        message = f"成功修复 {repaired_count} 条云端原版提示词，恢复 {restored_local} 条本地提示词，清理 {cleaned_count} 条格式异常词。"

    return {
        "status": status,
        "repaired": repaired_count,
        "restored_local": restored_local,
        "cleaned": cleaned_count,
        "projects_failed": len(failed_projects),
        "errors": [item["error"] for item in failed_projects],
        "failed_projects": failed_projects,
        "message": message,
    }
