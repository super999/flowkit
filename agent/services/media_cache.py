"""Local Media Cache Service — persist FlowKit images locally to prevent CDN URL expiry."""
import asyncio
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import aiohttp

from agent.config import MEDIA_CACHE_DIR

logger = logging.getLogger("flowkit.media_cache")

# Ensure cache directory exists
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_cached_image_path(media_id: str) -> Optional[Path]:
    """Return local cached Path if media_id exists and has valid size."""
    if not media_id:
        return None
    # Support jpg / png / webp
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        p = MEDIA_CACHE_DIR / f"{media_id}{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def get_cached_image_url(media_id: str) -> Optional[str]:
    """Return static web URL path if locally cached (e.g., /output/_cache/{media_id}.jpg)."""
    p = get_cached_image_path(media_id)
    if p:
        return f"/output/_cache/{p.name}"
    return None


async def download_to_file(url: str, dest_path: Path) -> bool:
    """Download image binary from url and save atomically to dest_path."""
    if not url or not url.startswith("http"):
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        allowed = ("flow-content.google", "storage.googleapis.com", "googleusercontent.com", "google.com")
        if not any(host.endswith(h) for h in allowed):
            logger.warning("download_to_file: rejected host %s", host)
            return False

        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 0:
                        tmp_path = dest_path.with_suffix(f".tmp_{dest_path.suffix}")
                        tmp_path.write_bytes(data)
                        tmp_path.replace(dest_path)
                        logger.info("Cached media to %s (%d bytes)", dest_path.name, len(data))
                        return True
                else:
                    logger.debug("download_to_file failed HTTP %d for %s", resp.status, url[:60])
    except Exception as e:
        logger.debug("download_to_file exception for %s: %s", url[:60], e)
    return False


async def cache_media_image(media_id: str, url: Optional[str] = None) -> Optional[Path]:
    """Cache media image to local disk. Resolves new CDN URL if input URL is expired."""
    if not media_id:
        return None

    existing = get_cached_image_path(media_id)
    if existing:
        return existing

    dest_path = MEDIA_CACHE_DIR / f"{media_id}.jpg"

    # 1. Try provided url first
    if url:
        success = await download_to_file(url, dest_path)
        if success:
            return dest_path

    # 2. If url missing or failed (403 expired), resolve fresh signed URL via FlowClient
    try:
        from agent.services.flow_client import get_flow_client
        client = get_flow_client()
        if client and client.connected:
            fresh_url = await client.resolve_media_url(media_id, timeout=12)
            if fresh_url and fresh_url != url:
                success = await download_to_file(fresh_url, dest_path)
                if success:
                    return dest_path
    except Exception as e:
        logger.debug("cache_media_image: resolve failed for %s: %s", media_id, e)

    return None


def trigger_background_cache(media_id: str, url: Optional[str] = None):
    """Fire-and-forget background task to cache a media item without blocking."""
    if not media_id:
        return
    if get_cached_image_path(media_id):
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cache_media_image(media_id, url))
    except RuntimeError:
        pass


fetch_and_cache_media = cache_media_image
