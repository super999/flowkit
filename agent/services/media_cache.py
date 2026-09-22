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

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
_IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",           # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"GIF87a",                  # GIF (kept for old user uploads)
    b"GIF89a",
)


def _looks_like_image(data: bytes) -> bool:
    """Return whether *data* starts with a supported raster image format."""
    if not data:
        return False
    if any(data.startswith(signature) for signature in _IMAGE_SIGNATURES):
        return True
    # WebP is a RIFF container whose type marker is at bytes 8..12.
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _looks_like_video(data: bytes) -> bool:
    """Return whether *data* looks like an ISO-BMFF video (usually MP4)."""
    if not data:
        return False
    # Most MP4 files put `ftyp` at offset 4.  A few Flow responses prepend a
    # small free/size box, so allow the marker anywhere in the first 64 bytes.
    return b"ftyp" in data[:64]


def _looks_like_error_page(data: bytes, content_type: str = "") -> bool:
    """Detect HTML/JSON error bodies returned with HTTP 200 by a CDN/proxy."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in {"text/html", "application/xhtml+xml", "application/json", "text/json"}:
        return True

    sample = data[:512].lstrip().lower()
    return (
        sample.startswith(b"<!doctype html")
        or sample.startswith(b"<html")
        or sample.startswith(b"<?xml") and b"<html" in sample
        or sample.startswith(b"{") and b"error" in sample[:256]
    )


def _is_valid_media_bytes(data: bytes, suffix: str = "", content_type: str = "") -> bool:
    """Validate downloaded/cache bytes without decoding the whole media file."""
    if not data or _looks_like_error_page(data, content_type):
        return False

    ext = (suffix or "").lower()
    if ext in _VIDEO_EXTENSIONS:
        return _looks_like_video(data)
    if ext in _IMAGE_EXTENSIONS:
        return _looks_like_image(data)
    # Unknown extension: accept only known media signatures.
    return _looks_like_image(data) or _looks_like_video(data)


def is_valid_media_file(path: Path) -> bool:
    """Return whether a local cache file contains media rather than an error page.

    This is intentionally a small header check.  It is safe for large videos
    and catches the common failure where a proxy returns an HTML login/error
    page with status 200.
    """
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with path.open("rb") as handle:
            head = handle.read(512)
        return _is_valid_media_bytes(head, path.suffix)
    except OSError:
        return False


def get_cached_image_path(media_id: str) -> Optional[Path]:
    """Return local cached Path only if it contains a valid image."""
    if not media_id:
        return None
    # Support jpg / png / webp
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        p = MEDIA_CACHE_DIR / f"{media_id}{ext}"
        if is_valid_media_file(p):
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
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        allowed = ("flow-content.google", "storage.googleapis.com", "googleusercontent.com", "google.com")
        if not any(host == h or host.endswith(f".{h}") for h in allowed):
            logger.warning("download_to_file: rejected host %s", host)
            return False

        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    content_type = resp.headers.get("Content-Type", "")
                    if _is_valid_media_bytes(data, dest_path.suffix, content_type):
                        tmp_path = dest_path.with_suffix(f".tmp_{dest_path.suffix}")
                        tmp_path.write_bytes(data)
                        tmp_path.replace(dest_path)
                        logger.info("Cached media to %s (%d bytes)", dest_path.name, len(data))
                        return True
                    logger.warning(
                        "download_to_file: rejected invalid media response for %s (content-type=%s, bytes=%d)",
                        dest_path.name,
                        content_type or "unknown",
                        len(data),
                    )
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
    if url and "/asb/" not in url:
        success = await download_to_file(url, dest_path)
        if success:
            return dest_path

    # 2. If url missing or failed (403 expired), resolve fresh signed URL via FlowClient
    try:
        from agent.services.flow_client import get_flow_client
        client = get_flow_client()
        if client and client.connected:
            fresh_url = await client.resolve_media_url(media_id, timeout=60)
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
