"""
Flow Client — communicates with Google Flow via the Chrome extension bridge.

Agent runs a WS server. Extension connects as client. Agent sends requests,
extension executes them in browser context (residential IP, cookies, reCAPTCHA).

One transport: Flow's ``batchexecute`` endpoint on flow.google.com, whose calls
only a signed-in page can sign — the agent builds the envelope, the extension
runs it in the tab (see :mod:`agent.services.flow_batch`).

The REST path against ``aisandbox-pa.googleapis.com`` that preceded it is gone.
It needed a ``Bearer ya29.…`` that Flow stopped minting in the September 2026
migration, so it could not run; keeping it only gave the next contributor a
second place to implement things. Git history has it if a payload is ever needed.

Answers are shaped like the old REST ones, so everything downstream — the
worker's parsers, the operation poller, the scene/character updaters — reads one
shape and never learns where it came from.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from agent.config import (
    VIDEO_MODELS,
    FLOW_PROJECT_ID, FLOW_ALLOW_DEGRADED,
    DEFAULT_PAYGATE_TIER,
)
from agent import config as _config
from agent.services import flow_batch as fb

logger = logging.getLogger(__name__)


def clean_flow_prompt_text(p: str) -> str:
    """Clean translation prefixes and quotes from Flow generated prompts."""
    if not p:
        return ""
    p = p.strip()
    prefixes = [
        "Here's the English translation of your image prompt:",
        "Here's the English translation of the image prompt:",
        "Here's the English translation of your video prompt:",
        "Here's the English translation of the video prompt:",
        "Here's the English translation of your prompt:",
        "Here's the English translation of the prompt:",
        "Here's the English translation:",
        "Here is the English translation of your image prompt:",
        "Here is the English translation of the image prompt:",
        "Here is the English translation of your video prompt:",
        "Here is the English translation of the video prompt:",
        "Here is the English translation of your prompt:",
        "Here is the English translation of the prompt:",
        "Here is the English translation:",
    ]
    p_lower = p.lower()
    for prefix in prefixes:
        pref_lower = prefix.lower()
        if pref_lower in p_lower:
            idx = p_lower.find(pref_lower)
            rest = p[idx + len(prefix):].strip()
            if (rest.startswith('"') and rest.endswith('"')) or (rest.startswith("'") and rest.endswith("'")):
                rest = rest[1:-1].strip()
            return rest
    return p


def extract_prompt_pair_from_media(item: dict | list, wf: dict = None) -> tuple[str, Optional[str]]:
    """Extract original and translated prompts from REST or wire media data."""
    if isinstance(item, (list, tuple)):
        return _extract_wire_prompt_pair(item, wf)

    meta = item.get("mediaMetadata", {}) or item.get("metadata", {}) if isinstance(item, dict) else {}
    req_data = meta.get("requestData", {}) if isinstance(meta, dict) else {}
    prompt_inputs = req_data.get("promptInputs", []) if isinstance(req_data, dict) else []
    orig_prompt = ""
    trans_prompt = ""

    if prompt_inputs and isinstance(prompt_inputs, list):
        pi = prompt_inputs[0]
        if isinstance(pi, dict):
            sp = pi.get("structuredPrompt", {})
            if isinstance(sp, dict):
                parts = sp.get("parts", [])
                if isinstance(parts, list):
                    texts = [p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]
                    if texts:
                        orig_prompt = "".join(texts).strip()
            trans_prompt = pi.get("textInput", "").strip()

    if not orig_prompt:
        media_title = meta.get("mediaTitle", "").strip() if isinstance(meta, dict) else ""
        if media_title:
            orig_prompt = media_title
        elif wf and isinstance(wf, dict):
            wf_meta = wf.get("metadata", {}) or wf.get("mediaMetadata", {})
            wf_disp = wf_meta.get("displayName", "").strip() if isinstance(wf_meta, dict) else ""
            wf_user = wf.get("userPrompt", "").strip()
            orig_prompt = wf_user or wf_disp

    img = item.get("image", {}) or (wf.get("image", {}) if isinstance(wf, dict) else {})
    vid = item.get("video", {}) or (wf.get("video", {}) if isinstance(wf, dict) else {})
    gen_img = img.get("generatedImage", {}) if isinstance(img, dict) else {}
    gen_vid = vid.get("generatedVideo", {}) if isinstance(vid, dict) else {}
    model_prompt = (
        gen_img.get("prompt", "") or gen_vid.get("prompt", "") or
        (img.get("prompt", "") if isinstance(img, dict) else "") or
        (vid.get("prompt", "") if isinstance(vid, dict) else "") or
        (wf.get("prompt", "") if isinstance(wf, dict) else "")
    ).strip()
    if not trans_prompt and model_prompt:
        trans_prompt = model_prompt
    cleaned_trans = clean_flow_prompt_text(trans_prompt)
    cleaned_orig = clean_flow_prompt_text(orig_prompt)
    if cleaned_orig and cleaned_trans and cleaned_orig != cleaned_trans:
        return cleaned_orig, cleaned_trans
    if cleaned_orig:
        return cleaned_orig, None
    if cleaned_trans:
        return cleaned_trans, None
    if model_prompt:
        return clean_flow_prompt_text(model_prompt), None
    return "", None


def _first_wire_text(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for child in value:
            text = _first_wire_text(child)
            if text:
                return text
    if isinstance(value, dict):
        for child in value.values():
            text = _first_wire_text(child)
            if text:
                return text
    return ""


def _extract_wire_prompt_pair(item: list | tuple, wf: Any = None) -> tuple[str, Optional[str]]:
    """Read original/translated prompts from Flow's positional media record."""
    meta = item[5] if len(item) > 5 else None
    details = item[6] if len(item) > 6 else None
    video_details = item[7] if len(item) > 7 else None
    if not isinstance(details, list) and isinstance(video_details, list):
        details = video_details
    original = ""
    translated = ""

    if isinstance(meta, list) and len(meta) > 6:
        # Video records carry the complete original prompt in meta[1].
        if isinstance(video_details, list) and len(meta) > 1 and isinstance(meta[1], str):
            original = meta[1].strip()
        prompt_meta = meta[6]
        prompt_inputs = prompt_meta[2] if isinstance(prompt_meta, list) and len(prompt_meta) > 2 else None
        if isinstance(prompt_inputs, list):
            for prompt_input in prompt_inputs:
                if isinstance(prompt_input, list) and prompt_input:
                    candidate = prompt_input[0]
                    if not isinstance(candidate, str) and len(prompt_input) > 2:
                        candidate = _first_wire_text(prompt_input[2])
                elif isinstance(prompt_input, dict):
                    candidate = (
                        prompt_input.get("textInput")
                        or prompt_input.get("prompt")
                        or prompt_input.get("text")
                    )
                else:
                    candidate = None
                if isinstance(candidate, str) and candidate.strip():
                    if not original:
                        original = candidate.strip()
                    break

    if isinstance(details, list) and details:
        first_detail = details[0]
        if isinstance(first_detail, list) and len(first_detail) > 7 and isinstance(first_detail[7], str):
            translated = first_detail[7].strip()

    # Workflow titles are names, not prompts.  Only decoded REST workflow
    # metadata is allowed as a fallback here; positional workflow titles stay
    # separate and are used for the media name by fetch_project_media().
    if not original and isinstance(wf, dict):
        original = (
            str(wf.get("userPrompt") or "").strip()
            or str((wf.get("metadata") or {}).get("displayName") or "").strip()
        )

    cleaned_orig = clean_flow_prompt_text(original)
    cleaned_trans = clean_flow_prompt_text(translated)
    if cleaned_orig and cleaned_trans and cleaned_orig != cleaned_trans:
        return cleaned_orig, cleaned_trans
    if cleaned_orig:
        return cleaned_orig, None
    if cleaned_trans:
        return cleaned_trans, None
    return "", None


def _is_flow_uuid(value: Any) -> bool:
    return isinstance(value, str) and FlowClient._UUID_RE.match(value) is not None


def _wire_timestamp(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, (int, float)):
        return value if isinstance(value, str) and value else None
    seconds = float(value)
    if seconds > 10_000_000_000:
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _wire_workflow_index(payload: Any) -> tuple[dict[str, dict], dict[str, dict]]:
    by_workflow: dict[str, dict] = {}
    by_media: dict[str, dict] = {}
    workflows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    if not isinstance(workflows, list):
        return by_workflow, by_media
    for workflow in workflows:
        if not isinstance(workflow, (list, tuple)):
            continue
        workflow_id = workflow[0] if workflow and isinstance(workflow[0], str) else ""
        block = workflow[3] if len(workflow) > 3 else None
        title = block[0].strip() if isinstance(block, list) and block and isinstance(block[0], str) else ""
        media_id = block[4] if isinstance(block, list) and len(block) > 4 and _is_flow_uuid(block[4]) else ""
        info = {"title": title, "workflow": workflow}
        if workflow_id:
            by_workflow[workflow_id] = info
        if media_id:
            by_media[media_id] = info
    return by_workflow, by_media


def _wire_media_entries(payload: Any):
    direct = payload[2] if isinstance(payload, list) and len(payload) > 2 else None
    if isinstance(direct, list):
        for entry in direct:
            if (
                isinstance(entry, (list, tuple))
                and len(entry) > 6
                and _is_flow_uuid(entry[0])
                and (
                    isinstance(entry[5], (list, dict))
                    or isinstance(entry[6], (list, dict))
                    or (len(entry) > 7 and isinstance(entry[7], (list, dict)))
                )
            ):
                yield entry


def _wire_urls(entry: list | tuple) -> tuple[str, str]:
    urls: list[str] = []

    def add(value: Any):
        if isinstance(value, str) and value.startswith("https://") and value not in urls:
            urls.append(value)

    meta = entry[5] if len(entry) > 5 else None
    if isinstance(meta, list):
        for index in (5, 10):
            if len(meta) > index:
                add(meta[index])

    details = entry[6] if len(entry) > 6 else None
    if isinstance(details, list):
        upload_detail = details[1] if len(details) > 1 else None
        if isinstance(upload_detail, list) and len(upload_detail) > 3:
            add(upload_detail[3])
        for text in fb._walk_strings(details):
            add(text)

    video_details = entry[7] if len(entry) > 7 else None
    if isinstance(video_details, list):
        for text in fb._walk_strings(video_details):
            add(text)

    video = next((url for url in urls if "/video/" in url), "")
    image = next((url for url in urls if "/image/" in url), "")
    if not image:
        image = next((url for url in urls if "googleusercontent.com" in url), "")
    return video, image


def _wire_is_video(entry: list | tuple) -> bool:
    video_details = entry[7] if len(entry) > 7 else None
    return isinstance(video_details, list) and bool(video_details) and isinstance(video_details[0], list)


def _wire_aspect(details: Any, media_type: str) -> str:
    value = None
    if isinstance(details, list) and details:
        first = details[0]
        ratio_index = 16 if media_type == "VIDEO" else 14
        if isinstance(first, list) and len(first) > ratio_index:
            value = first[ratio_index]
    if not isinstance(value, int):
        return str(value) if value is not None else ""
    if media_type == "VIDEO":
        return {1: "VIDEO_ASPECT_RATIO_PORTRAIT", 2: "VIDEO_ASPECT_RATIO_LANDSCAPE"}.get(value, str(value))
    return {
        1: "IMAGE_ASPECT_RATIO_SQUARE",
        2: "IMAGE_ASPECT_RATIO_PORTRAIT",
        3: "IMAGE_ASPECT_RATIO_LANDSCAPE",
        4: "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE",
        5: "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    }.get(value, str(value))


def _wire_model_name(details: Any) -> str:
    if isinstance(details, list) and details:
        first = details[0]
        if isinstance(first, list) and len(first) > 12 and isinstance(first[12], str):
            return first[12]
    return ""

# Captured from the current Flow image composer. x4 launches independent
# ogiZ0b requests at roughly 0.0s, 0.5s, 1.5s and 2.5s rather than bursting
# all variants at once. The generation work still overlaps after submission.
IMAGE_UI_SUBMIT_OFFSETS_S = (0.0, 0.5, 1.5, 2.5)

# RPC [8] is a transient Flow-side generation rejection seen under image load.
# A short 6s retry was still rejected in live testing, so use one bounded
# cooldown retry rather than hot-looping or multiplying duplicate generations.
# This is FlowKit resilience policy; the current UI was not observed to retry
# automatically after the same failure.
IMAGE_TRANSIENT_RETRY_DELAY_S = 34.0
IMAGE_TRANSIENT_MAX_ATTEMPTS = 2


class FlowClient:
    """Sends commands to Chrome extension via WebSocket."""

    def __init__(self):
        self._extension_ws = None  # Active authenticated extension connection
        self._extensions: dict[object, dict] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_ws: dict[str, object] = {}
        self._flow_key: Optional[str] = None
        # Per-operation poll state. `_operation_projects` says which project
        # listing to look a finished media up in; `_operation_media` caches the
        # id once the listing has it, so later rounds skip the listing entirely;
        # `_operation_polls` counts rounds, to keep the listing off most of them.
        self._operation_projects: dict[str, str] = {}
        self._operation_media: dict[str, str] = {}
        self._operation_polls: dict[str, int] = {}
        # WS stats
        self._ws_connect_count = 0
        self._ws_disconnect_count = 0
        self._ws_connected_at: Optional[float] = None
        self._ws_last_disconnect_at: Optional[float] = None

    def set_extension(self, ws):
        """Called when extension connects via WS."""
        self._extensions[ws] = {
            "connected_at": time.time(),
            "flow_key": None,
            "token_captured_at": None,
            "extension_version": None,
            "flow_url_supported": None,
            "unavailable_until": 0,
        }
        # A new unauthenticated profile must not displace an already
        # authenticated extension. It becomes active after token_captured.
        if self._extension_ws is None:
            self._extension_ws = ws
        self._ws_connect_count += 1
        self._ws_connected_at = time.time()
        logger.info(
            "Extension connected #%d (%d active connection(s)); "
            "waiting for extension_ready/token_captured to sync",
            self._ws_connect_count,
            len(self._extensions),
        )

    def clear_extension(self, ws=None):
        """Called when extension disconnects."""
        disconnected_ws = ws or self._extension_ws
        if disconnected_ws is None:
            return

        self._extensions.pop(disconnected_ws, None)
        self._ws_disconnect_count += 1
        self._ws_last_disconnect_at = time.time()

        # Only cancel requests that were sent through the disconnected socket.
        # Requests owned by other Chrome profiles are still valid.
        disconnected_pending = [
            (req_id, self._pending.get(req_id))
            for req_id, pending_ws in list(self._pending_ws.items())
            if pending_ws is disconnected_ws
        ]
        for req_id, future in disconnected_pending:
            if future is not None and not future.done():
                future.set_exception(ConnectionError("Extension disconnected"))
            self._pending_ws.pop(req_id, None)

        if self._extension_ws is disconnected_ws:
            self._extension_ws = self._select_extension(require_token=True)
            if self._extension_ws is None:
                self._extension_ws = self._select_extension(require_token=False)

        active_session = self._extensions.get(self._extension_ws, {})
        self._flow_key = active_session.get("flow_key")
        logger.warning(
            "Extension disconnected, cancelled %d owned request(s); "
            "%d extension connection(s) remain",
            len(disconnected_pending),
            len(self._extensions),
        )

    def _extension_candidates(self, require_token: bool):
        """Return usable extensions in preferred routing order."""
        now = time.time()
        candidates = []
        for ws, session in self._extensions.items():
            if require_token and not session.get("flow_key"):
                continue
            recency = (
                session.get("token_captured_at")
                if require_token
                else session.get("connected_at")
            )
            candidates.append({
                "ws": ws,
                "available": session.get("unavailable_until", 0) <= now,
                "active": ws is self._extension_ws,
                "recency": recency or 0,
            })

        # Prefer an available active session, then the most recently
        # authenticated alternatives. Temporarily unavailable sessions remain
        # last-resort candidates so a single-profile setup can still recover.
        candidates.sort(
            key=lambda item: (
                item["available"],
                item["active"] and item["available"],
                item["recency"],
            ),
            reverse=True,
        )
        return [item["ws"] for item in candidates]

    def _select_extension(self, require_token: bool):
        """Choose the preferred authenticated or connected extension."""
        candidates = self._extension_candidates(require_token)
        return candidates[0] if candidates else None

    @staticmethod
    def _should_failover(result: dict) -> bool:
        """Return true for profile-local failures that another tab can solve."""
        message = str(result.get("error") or result.get("data") or "").lower()
        return any(marker in message for marker in (
            "no_flow_key",
            "no_flow_tab",
            # Batch path: this profile's Flow tab cannot sign a request — it is
            # signed out, still booting, or Chrome discarded it. Another
            # profile's tab may be perfectly able to.
            "no_at_token",
            "flow_tab_discarded",
            "no current window",
            "extension not connected",
            "extension disconnected",
            "extension_switched",
            "public_error_per_model_daily_quota_reached",
            "public_error_user_quota_reached",
        ))

    def set_flow_key(self, key: str):
        self._flow_key = key
        if self._extension_ws in self._extensions:
            self._extensions[self._extension_ws]["flow_key"] = key
            self._extensions[self._extension_ws]["token_captured_at"] = time.time()

    @property
    def connected(self) -> bool:
        return bool(self._extensions)

    @property
    def ws_stats(self) -> dict:
        uptime = None
        if self._ws_connected_at and self.connected:
            uptime = int(time.time() - self._ws_connected_at)
        versions = sorted({
            str(session["extension_version"])
            for session in self._extensions.values()
            if session.get("extension_version")
        })
        flow_url_support = [
            session.get("flow_url_supported")
            for session in self._extensions.values()
            if session.get("flow_url_supported") is not None
        ]
        return {
            "connected": self.connected,
            "active_connections": len(self._extensions),
            "authenticated_connections": sum(
                1 for session in self._extensions.values()
                if session.get("flow_key")
            ),
            "extension_versions": versions,
            "flow_url_supported": all(flow_url_support) if flow_url_support else None,
            "connects": self._ws_connect_count,
            "disconnects": self._ws_disconnect_count,
            "uptime_s": uptime,
        }

    async def handle_message(self, data: dict, websocket=None):
        """Handle incoming message from extension."""
        if data.get("type") == "token_captured":
            key = data.get("flowKey")
            source_ws = websocket or self._extension_ws
            if source_ws is not None and source_ws in self._extensions:
                self._extensions[source_ws]["flow_key"] = key
                self._extensions[source_ws]["token_captured_at"] = time.time()
                self._extension_ws = source_ws
            self._flow_key = key
            logger.info("Flow key captured from extension")
            asyncio.create_task(self._sync_tier())
            return

        if data.get("type") == "extension_ready":
            source_ws = websocket or self._extension_ws
            version = data.get("extensionVersion")
            flow_supported = data.get("flowUrlSupported")
            if source_ws is not None and source_ws in self._extensions:
                self._extensions[source_ws]["extension_version"] = version
                self._extensions[source_ws]["flow_url_supported"] = flow_supported
            logger.info(
                "Extension ready, flowKey=%s version=%s flow.google.com=%s",
                "yes" if data.get("flowKeyPresent") else "no",
                version or "unknown",
                "yes" if flow_supported is True else "no" if flow_supported is False else "unknown",
            )
            asyncio.create_task(self._sync_tier())
            return

        if data.get("type") == "media_urls_refresh":
            asyncio.create_task(self._refresh_media_urls(data.get("urls", [])))
            return

        if data.get("type") == "pong":
            return

        if data.get("type") == "ping":
            # Respond to keepalive
            target_ws = websocket or self._extension_ws
            if target_ws:
                await target_ws.send(json.dumps({"type": "pong"}))
            return

        # Response to a pending request
        req_id = data.get("id")
        if req_id and req_id in self._pending:
            if not self._pending[req_id].done():
                self._pending[req_id].set_result(data)
            return

    async def _sync_tier(self):
        """Detect current tier from credits API and update all active projects."""
        if getattr(self, '_sync_in_progress', False):
            return
        self._sync_in_progress = True
        try:
            result = await self.get_credits()
            data = result.get("data", result)
            tier = data.get("userPaygateTier", "PAYGATE_TIER_ONE")
            logger.info("Syncing tier: %s", tier)

            from agent.db import crud
            projects = await crud.list_projects(status="ACTIVE")
            for p in projects:
                if p.get("user_paygate_tier") != tier:
                    await crud.update_project(p["id"], user_paygate_tier=tier)
                    logger.info("Updated project %s tier: %s -> %s",
                                p["id"][:12], p.get("user_paygate_tier"), tier)
        except Exception as e:
            logger.warning("Failed to sync tier: %s", e)
        finally:
            self._sync_in_progress = False

    _UUID_RE = __import__("re").compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    # flow-content.google is where the rewritten frontend serves media from;
    # the other two are the pre-migration hosts, still seen on older media.
    _SAFE_URL_RE = __import__("re").compile(
        r'^https://(storage\.googleapis\.com|lh3\.googleusercontent\.com|flow-content\.google)/')

    async def _refresh_media_urls(self, urls: list[dict]):
        """Update scene/character URLs in DB from fresh TRPC-captured signed URLs.

        Each entry: {mediaId: str, mediaType: 'image'|'video', url: str}
        """
        from agent.db import crud
        from agent.services.event_bus import event_bus

        updated = 0
        for entry in urls:
            media_id = entry.get("mediaId", "")
            media_type = entry.get("mediaType", "")
            url = entry.get("url", "")
            if not media_id or not url:
                continue
            # Validate media_id is UUID and url is from trusted domains
            if not self._UUID_RE.match(media_id):
                logger.warning("Rejected invalid media_id: %s", media_id[:20])
                continue
            if not self._SAFE_URL_RE.match(url):
                logger.warning("Rejected untrusted URL domain for media %s", media_id[:12])
                continue
            if media_type not in ("image", "video"):
                continue

            # Try matching against scenes (check both orientations)
            scenes = await crud.list_scenes_by_media_id(media_id)
            for scene in scenes:
                updates = {}
                if media_type == "image":
                    # Update whichever orientation matches
                    if scene.get("vertical_image_media_id") == media_id:
                        updates["vertical_image_url"] = url
                    if scene.get("horizontal_image_media_id") == media_id:
                        updates["horizontal_image_url"] = url
                elif media_type == "video":
                    if scene.get("vertical_video_media_id") == media_id:
                        updates["vertical_video_url"] = url
                    if scene.get("horizontal_video_media_id") == media_id:
                        updates["horizontal_video_url"] = url
                    if scene.get("vertical_upscale_media_id") == media_id:
                        updates["vertical_upscale_url"] = url
                    if scene.get("horizontal_upscale_media_id") == media_id:
                        updates["horizontal_upscale_url"] = url
                if updates:
                    await crud.update_scene(scene["id"], **updates)
                    updated += 1

            # Try matching against characters
            chars = await crud.list_characters_by_media_id(media_id)
            for char in chars:
                if media_type == "image" and char.get("media_id") == media_id:
                    await crud.update_character(char["id"], reference_image_url=url)
                    updated += 1

        if updated:
            logger.info("Refreshed %d media URLs from TRPC intercept", updated)
            await event_bus.emit("urls_refreshed", {"count": updated})

    async def refresh_project_urls(self, project_id: str) -> dict:
        """Re-sign every stored media url for a project.

        The batch path can do this properly: the media rpc answers a media id
        with a freshly signed url, so we walk the project's scenes and entities
        and refresh each id we hold.
        """
        from agent.db import crud

        # (media_id, kind) -> the scene/character fields it should land in
        targets: dict[tuple[str, str], list[tuple[str, str, str]]] = {}

        def want(media_id, kind, table, row_id, field):
            if media_id and self._UUID_RE.match(media_id):
                targets.setdefault((media_id, kind), []).append((table, row_id, field))

        scenes = []
        for video in await crud.list_videos(project_id):
            scenes.extend(await crud.list_scenes(video["id"]))

        for scene in scenes:
            for prefix in ("vertical", "horizontal"):
                want(scene.get(f"{prefix}_image_media_id"), "image",
                     "scene", scene["id"], f"{prefix}_image_url")
                want(scene.get(f"{prefix}_video_media_id"), "video",
                     "scene", scene["id"], f"{prefix}_video_url")
                want(scene.get(f"{prefix}_upscale_media_id"), "video",
                     "scene", scene["id"], f"{prefix}_upscale_url")
        for char in await crud.get_project_characters(project_id):
            want(char.get("media_id"), "image", "character", char["id"], "reference_image_url")

        refreshed = 0
        for (media_id, kind), fields in targets.items():
            try:
                urls = await self._batch_media_urls(media_id)
            except Exception as e:
                logger.warning("Refresh failed for media %s: %s", media_id[:12], e)
                continue
            url = urls.video if kind == "video" else urls.image
            if not url:
                continue
            for table, row_id, field in fields:
                if table == "scene":
                    await crud.update_scene(row_id, **{field: url})
                else:
                    await crud.update_character(row_id, **{field: url})
                refreshed += 1

        logger.info("Refreshed %d/%d media urls for project %s",
                    refreshed, len(targets), project_id[:12])
        return {"refreshed": refreshed, "found": len(targets)}

    async def _send(self, method: str, params: dict, timeout: float = 300) -> dict:
        """Send request to extension and wait for response.

        Always returns a dict. On error, returns {"error": "<reason>"} — callers
        must check result.get("error") or use _is_ws_error() before reading data.
        Never raises; exceptions are caught and returned as error dicts.
        """
        if not self.connected:
            return {"error": "Extension not connected"}

        # No profile needs a bearer any more: batchexecute authenticates in the
        # page with the session cookie. Demanding a flow key here would reject
        # every profile, because none on this path ever captures one.
        extension_candidates = self._extension_candidates(require_token=False)
        if not extension_candidates:
            return {"error": "Extension not connected"}

        last_result = {"error": "Extension not connected"}
        for index, extension_ws in enumerate(extension_candidates):
            if extension_ws not in self._extensions:
                continue

            self._extension_ws = extension_ws
            self._flow_key = self._extensions[extension_ws].get("flow_key")
            req_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = future
            self._pending_ws[req_id] = extension_ws

            try:
                await extension_ws.send(json.dumps({
                    "id": req_id,
                    "method": method,
                    "params": params,
                }))
                last_result = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                last_result = {"error": f"Timeout ({timeout}s) waiting for {method}"}
            except Exception as e:
                last_result = {"error": str(e)}
            finally:
                self._pending.pop(req_id, None)
                self._pending_ws.pop(req_id, None)

            has_alternative = index + 1 < len(extension_candidates)
            if self._should_failover(last_result) and has_alternative:
                if extension_ws in self._extensions:
                    self._extensions[extension_ws]["unavailable_until"] = (
                        time.time() + 60
                    )
                logger.warning(
                    "Extension profile unavailable for %s; retrying through "
                    "another authenticated profile",
                    method,
                )
                continue

            return last_result

        return last_result

    # ─── batchexecute transport ──────────────────────────────
    #
    # Flow's rewritten frontend signs every call with the session cookie plus a
    # per-page `at` token, and a generate also carries a single-use reCAPTCHA.
    # None of that can be replayed from here, so the agent builds the envelope
    # and the extension runs it inside a signed-in flow.google.com tab.

    async def batch_rpc(self, rpcid: str, freq: str,
                        captcha_action: str | None = None,
                        match: str | None = None,
                        timeout: float = 300) -> dict:
        """Run one batchexecute RPC in the Flow page. Returns the raw body.

        ``match`` asks the extension to cut the response down to an 800-byte
        window around that string before handing it back. The project listing
        is tens of megabytes for the one entry we want, and the cheapest place
        to throw the rest away is inside the tab.
        """
        params: dict = {"rpcid": rpcid, "freq": freq}
        if captcha_action:
            params["captchaAction"] = captcha_action
        if match:
            params["match"] = match
        return await self._send("batch_rpc", params, timeout=timeout)

    async def _batch_payload(self, rpcid: str, freq: str,
                             captcha_action: str | None = None,
                             timeout: float = 300):
        """One RPC, unwrapped to its inner payload. Raises on anything else."""
        result = await self.batch_rpc(rpcid, freq, captcha_action, timeout=timeout)
        if result.get("error"):
            raise fb.FlowBatchError(f"{rpcid}: {result['error']}")
        return fb.first_payload(result.get("data") or "", rpcid)

    def _batch_project_id(self, project_id: str) -> str:
        """The Flow project an RPC is scoped to.

        Flow Kit stores the Flow project uuid as the local project id, but a
        few call sites pass "0" or "" for project-less work; those fall back to
        the pinned FLOW_PROJECT_ID.
        """
        if project_id and self._UUID_RE.match(str(project_id)):
            return str(project_id)
        if FLOW_PROJECT_ID:
            return FLOW_PROJECT_ID
        raise fb.FlowBatchError(
            "NO_FLOW_PROJECT: every batchexecute call is scoped to a Flow project. "
            "Create one in the Flow UI and pin its uuid as FLOW_PROJECT_ID."
        )

    def _batch_image_model(self, override: str | None = None) -> str:
        # Read through the module: PATCH /api/models hot-reloads both of these.
        nickname = _config.DEFAULT_IMAGE_MODEL
        return fb.resolve_image_model(
            override or _config.IMAGE_MODELS.get(nickname) or nickname
        )

    def _batch_video_model(self, tier: str, gen_type: str, aspect_ratio: str) -> str:
        legacy = VIDEO_MODELS.get(tier, {}).get(gen_type, {}).get(aspect_ratio)
        return fb.resolve_video_model(legacy)

    def _remember_operation(self, operation_id: str, project_id: str):
        """Which project an operation belongs to — the listing lookup needs it.

        A poll record usually carries the project id, but old operations decay
        to a bare id, so keep our own note. Bounded: this is a cache, and the
        pinned project is always a workable fallback.
        """
        if not operation_id:
            return
        if len(self._operation_projects) > 512:
            self._operation_projects.clear()
            self._operation_media.clear()
            self._operation_polls.clear()
        self._operation_projects[operation_id] = project_id

    # ─── High-level API Methods ──────────────────────────────

    def flow_project_id(self, requested: str | None = None) -> str | None:
        """The Flow project to attach a new Flow Kit project to, if any.

        Project creation went with the labs.google tRPC endpoint the migration
        unauthenticated, so on the batch path a project is made once in the
        Flow UI and its uuid supplied here or pinned as FLOW_PROJECT_ID.
        """
        if requested and self._UUID_RE.match(requested):
            return requested
        return FLOW_PROJECT_ID or None

    async def create_project(self, project_title: str, tool_name: str = "PINHOLE") -> dict:
        pid = self.flow_project_id()
        if not pid:
            return {"error": _UNSUPPORTED_CREATE_PROJECT}
        logger.info("Reusing pinned Flow project %s for '%s'", pid[:12], project_title)
        return {"status": 200, "data": {"projectId": pid}}

    async def search_user_projects(self, limit: int = 50, tool_name: str = "PINHOLE") -> list[dict]:
        """List locally linked projects plus the configured Flow project.

        The current batch frontend has no standalone project-search RPC.  The
        local project rows are therefore the authoritative names while the
        pinned Flow uuid remains available for media synchronization.
        """
        from agent.db import crud

        rows = await crud.list_projects()
        projects = [
            {"projectId": row["id"], "title": row["name"], "source": "local_link"}
            for row in rows
        ]
        if FLOW_PROJECT_ID and not any(item["projectId"] == FLOW_PROJECT_ID for item in projects):
            projects.append({"projectId": FLOW_PROJECT_ID, "title": FLOW_PROJECT_ID, "source": "pinned"})
        return projects[:limit]

    async def get_project(self, project_id: str, tool_name: str = "PINHOLE") -> dict:
        projects = await self.search_user_projects(limit=10000, tool_name=tool_name)
        return next((project for project in projects if project["projectId"] == project_id), {})

    async def fetch_project_media(self, project_id: str, limit: int | None = None) -> list[dict]:
        """Read complete current media records from ``Zzl0ze``.

        Flow returns media in ``payload[2]`` ordered by UUID.  A historical
        200-item cap therefore drops arbitrary records, so synchronization
        callers should leave ``limit`` as ``None``.
        """
        from agent.db import crud

        payload = await self._batch_payload(fb.RPC_PROJECT_MEDIA, fb.project_media_request(project_id))
        cached, _ = await crud.list_media_library(project_id=project_id, limit=10000)
        by_id = {row["media_id"]: row for row in cached}
        workflows_by_id, workflows_by_media = _wire_workflow_index(payload)
        result, seen = [], set()

        for entry in _wire_media_entries(payload):
            media_id = entry[0]
            if media_id in seen:
                continue
            seen.add(media_id)
            old = by_id.get(media_id, {})
            workflow_id = entry[2] if len(entry) > 2 and isinstance(entry[2], str) else ""
            workflow = workflows_by_id.get(workflow_id) or workflows_by_media.get(media_id) or {}
            prompt, translated = extract_prompt_pair_from_media(entry, workflow.get("workflow"))
            video_url, image_url = _wire_urls(entry)
            media_type = "VIDEO" if _wire_is_video(entry) or video_url else (old.get("media_type") or "IMAGE")
            embedded_url = video_url if media_type == "VIDEO" else (image_url or video_url)
            result.append({
                "mediaKey": media_id,
                "mediaType": media_type,
                "prompt": prompt or old.get("prompt") or "",
                "translated_prompt": translated or old.get("translated_prompt"),
                "modelName": _wire_model_name(entry[7] if len(entry) > 7 else None) or old.get("model_name") or "",
                "aspectRatio": _wire_aspect(
                    entry[7] if _wire_is_video(entry) else (entry[6] if len(entry) > 6 else None),
                    media_type,
                ) or old.get("aspect_ratio") or "",
                "url": embedded_url or old.get("url") or "",
                "createTime": _wire_timestamp(entry[5][0] if isinstance(entry[5], list) and entry[5] else None) or old.get("created_at"),
                "name": prompt or workflow.get("title") or old.get("name") or f"Flow {media_type} ({media_id[:6]})",
                "source": "batch_project_media",
            })

        # Legacy operation-listing shape remains useful during migration and
        # for old captures; new direct media records have already been seen.
        for node in fb._walk_lists(payload):
            if len(node) < 4 or not isinstance(node[0], str) or not self._UUID_RE.match(node[0]):
                continue
            detail = node[3]
            if not isinstance(detail, list) or len(detail) <= 4:
                continue
            media_id = detail[4]
            if not isinstance(media_id, str) or not self._UUID_RE.match(media_id) or media_id in seen:
                continue
            seen.add(media_id)
            old = by_id.get(media_id, {})
            media_type = old.get("media_type") or "IMAGE"
            fresh_url = old.get("url") or ""
            if not old.get("media_type"):
                urls = await self._batch_media_urls(media_id)
                media_type = "VIDEO" if urls.video else "IMAGE"
                fresh_url = urls.video or urls.image or fresh_url
            result.append({
                "mediaKey": media_id,
                "mediaType": media_type,
                "prompt": old.get("prompt") or "",
                "translated_prompt": old.get("translated_prompt"),
                "modelName": old.get("model_name") or "",
                "aspectRatio": old.get("aspect_ratio") or "",
                "url": fresh_url,
                "createTime": old.get("created_at"),
                "name": old.get("name") or detail[0] or f"Flow {media_type} ({media_id[:6]})",
                "source": "batch_listing_with_local_metadata",
            })

        return result[:limit] if limit is not None and limit > 0 else result

    async def fetch_user_history(self, limit: int = 100, history_type: str = "FLOW") -> list[dict]:
        result = []
        for project in await self.search_user_projects():
            remaining = max(1, limit - len(result))
            result.extend(await self.fetch_project_media(project["projectId"], limit=remaining))
            if len(result) >= limit:
                break
        return result[:limit]

    async def generate_images(self, prompt: str, project_id: str,
                               aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
                               user_paygate_tier: str = "PAYGATE_TIER_TWO",
                               character_media_ids: list[str] = None,
                               image_model: str = None,
                               count: int = 1,
                               seed: int | None = None,
                               base_media_id: str | None = None) -> dict:
        """Generate image(s).

        ``character_media_ids`` are attached as reference images, which is what
        keeps an entity the same across scenes. Response is shaped like the
        old REST one so the parsers downstream do not have to care which
        transport produced it.
        """

        try:
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 4:
                raise ValueError("image count must be an integer from 1 to 4")
            pid = self._batch_project_id(project_id)
            model = self._batch_image_model(image_model)
            refs = list(character_media_ids or []) or None

            async def submit_once(index: int, launch_offset: float = 0.0):
                if launch_offset:
                    await asyncio.sleep(launch_offset)
                request_seed = seed + index * 9973 if seed is not None else None
                freq = fb.image_request(
                    prompt, pid, count=1, aspect=aspect_ratio, seed=request_seed,
                    model=model, ref_media_ids=refs, base_media_id=base_media_id,
                )
                payload = await self._batch_payload(
                    fb.RPC_GEN_IMAGE, freq, fb.CAPTCHA_IMAGE
                )
                generated = fb.read_images(payload)
                if not generated:
                    raise fb.FlowBatchError("Image generation returned no media url")
                return generated[0]

            async def run_wave(indices: list[int]) -> dict[int, object]:
                # Flow's UI starts variants as separate single-image RPCs with a
                # short cadence instead of a burst. Apply the cadence relative
                # to each wave, while Google still performs the generation work
                # concurrently after each request has been accepted.
                tasks = [
                    submit_once(index, IMAGE_UI_SUBMIT_OFFSETS_S[position])
                    for position, index in enumerate(indices)
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                return dict(zip(indices, results))

            results = await run_wave(list(range(count)))
            retry_indices = [
                index for index, result in results.items()
                if isinstance(result, fb.RpcError)
                and result.rpcid == fb.RPC_GEN_IMAGE
                and result.detail == [8]
            ]
            if retry_indices:
                logger.warning(
                    "Flow image wave had transient [8] for variant(s) %s; "
                    "retrying after %.0fs cooldown once the first wave is fully settled",
                    ",".join(str(i + 1) for i in retry_indices),
                    IMAGE_TRANSIENT_RETRY_DELAY_S,
                )
                await asyncio.sleep(IMAGE_TRANSIENT_RETRY_DELAY_S)
                retried = await run_wave(retry_indices)
                results.update(retried)

            images_by_index = {
                index: result
                for index, result in results.items()
                if not isinstance(result, BaseException)
            }
            failures = {
                index: result
                for index, result in results.items()
                if isinstance(result, BaseException)
            }
            if not images_by_index:
                first_error = failures[min(failures)] if failures else fb.FlowBatchError(
                    "Image generation returned no media url"
                )
                raise first_error

            images = [images_by_index[index] for index in sorted(images_by_index)]

        except Exception as e:
            return _batch_error(e)

        data = {
            "media": [_as_media_record(i) for i in images],
            "requested_count": count,
            "generated_count": len(images),
            "complete": len(images) == count,
        }
        if failures:
            data["failed_variants"] = [
                {"index": index + 1, "error": str(error)}
                for index, error in sorted(failures.items())
            ]
        return {"status": 200, "data": data}

    async def edit_image(self, prompt: str, source_media_id: str,
                          project_id: str,
                          aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
                          user_paygate_tier: str = "PAYGATE_TIER_ONE",
                          character_media_ids: list[str] = None,
                          image_model: str = None,
                          count: int = 1,
                          seed: int | None = None) -> dict:
        """Edit an image with the source encoded as Flow's BASE_IMAGE input.

        Additional references remain REFERENCE inputs. Sending the source as a
        generic reference conditions a fresh generation; BASE_IMAGE is the wire
        shape the current Flow editor uses for an actual image edit/refine.
        """

        refs = [mid for mid in (character_media_ids or []) if mid != source_media_id]
        return await self.generate_images(
            prompt=prompt,
            project_id=project_id,
            aspect_ratio=aspect_ratio,
            user_paygate_tier=user_paygate_tier,
            character_media_ids=refs,
            image_model=image_model,
            count=count,
            seed=seed,
            base_media_id=source_media_id,
        )

    async def upscale_image(self, media_id: str, project_id: str,
                            resolution: str = "2K") -> dict:
        """Return Flow's synchronous 2K/4K image upscale as base64 JPEG data."""
        try:
            pid = self._batch_project_id(project_id)
            freq = fb.image_upscale_request(media_id, resolution)
            payload = await self._batch_payload(
                fb.RPC_UPSCALE_IMAGE,
                freq,
                fb.CAPTCHA_IMAGE,
                timeout=150,
            )
            encoded = fb.read_upscaled_image(payload)
        except Exception as e:
            return _batch_error(e)
        return {
            "status": 200,
            "data": {
                "media_id": media_id,
                "project_id": pid,
                "resolution": str(resolution).upper(),
                "encodedImage": encoded,
                "contentType": "image/jpeg",
            },
        }

    async def generate_video(self, start_image_media_id: str, prompt: str,
                              project_id: str, scene_id: str,
                              aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                              end_image_media_id: str = None,
                              user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Submit an i2v generation. Returns operations for the poller."""

        if end_image_media_id:
            if not FLOW_ALLOW_DEGRADED:
                return {"error": _unsupported(
                    "start+end frame chaining",
                    "the new payload's end-image slot was never captured",
                )}
            logger.warning(
                "Scene %s: dropping end frame %s — chaining is not on the batch path, "
                "running plain i2v because FLOW_ALLOW_DEGRADED=1",
                str(scene_id)[:12], end_image_media_id[:12])

        gen_type = "start_end_frame_2_video" if end_image_media_id else "frame_2_video"
        try:
            pid = self._batch_project_id(project_id)
            freq = fb.video_request(
                prompt, pid, start_image_media_id, aspect=aspect_ratio,
                model=self._batch_video_model(user_paygate_tier, gen_type, aspect_ratio),
            )
            payload = await self._batch_payload(
                fb.RPC_GEN_VIDEO, freq, fb.CAPTCHA_VIDEO, timeout=120)
            operation = fb.read_operation(payload)
        except Exception as e:
            return _batch_error(e)

        self._remember_operation(operation.operation_id, pid)
        return {"status": 200, "data": {"operations": [_as_pending_operation(operation.operation_id)]}}

    async def generate_video_from_references(self, reference_media_ids: list[str],
                                              prompt: str, project_id: str, scene_id: str,
                                              aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                                              user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Generate video from multiple reference images (r2v)."""

        if not FLOW_ALLOW_DEGRADED:
            return {"error": _unsupported(
                "reference-to-video (r2v)",
                "its payload was never captured off the new UI",
            )}
        if not reference_media_ids:
            return {"error": "No reference media_ids for r2v"}
        logger.warning(
            "Scene %s: r2v is not on the batch path — running i2v off the first "
            "reference %s because FLOW_ALLOW_DEGRADED=1",
            str(scene_id)[:12], reference_media_ids[0][:12])
        return await self.generate_video(
            start_image_media_id=reference_media_ids[0], prompt=prompt,
            project_id=project_id, scene_id=scene_id, aspect_ratio=aspect_ratio,
            user_paygate_tier=user_paygate_tier,
        )

    async def upscale_video(self, media_id: str, scene_id: str,
                             aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                             resolution: str = "VIDEO_RESOLUTION_4K") -> dict:
        """Upscale a video."""
        return {"error": _unsupported(
            "video upscale",
            "no upsampler rpc appears in the new frontend's captures",
        )}

    async def check_video_status(self, operations: list[dict]) -> dict:
        """One poll round for each submitted operation.

        Three signals have to agree before a clip can be downloaded, and they
        arrive out of order:

        * the operation poll says how the job is going — but it can sit at no
          status at all on a job that finished, and a "Media not found."
          complaint on it is survivable rather than fatal;
        * the project listing is what actually gains a media id;
        * the media record serves the poster image first and grows the
          ``/video/`` url in later.

        So an operation only reports SUCCESSFUL once there is a video url.
        Everything short of that is PENDING, and the caller's own poll loop
        owns the timeout.
        """

        out = []
        for entry in operations or []:
            op_id = (entry.get("operation") or {}).get("name") or entry.get("name") or ""
            if not op_id:
                out.append({"operation": {}, "status": "MEDIA_GENERATION_STATUS_FAILED",
                            "error": "operation carried no name"})
                continue
            try:
                out.append(await self._poll_batch_operation(op_id))
            except Exception as e:
                # A hiccup on one poll round costs a round, not the job.
                logger.warning("Operation %s poll failed: %s", op_id[:20], e)
                out.append(_as_pending_operation(op_id, error=str(e)))
        return {"status": 200, "data": {"operations": out}}

    async def _poll_batch_operation(self, operation_id: str) -> dict:
        media_id = self._operation_media.get(operation_id)
        complaint = None

        if not media_id:
            media_id, complaint = await self._find_operation_media(operation_id)
            if not media_id:
                return _as_pending_operation(operation_id, error=complaint)
            self._operation_media[operation_id] = media_id

        urls = await self._batch_media_urls(media_id)
        if not urls.video:
            # The id landed but the clip is still being written; downloading
            # now would save the poster still instead of the video.
            return _as_pending_operation(operation_id, error=complaint, media_id=media_id)

        # The media id stays cached rather than being cleared here: a batch
        # with several operations re-polls the finished ones alongside the
        # pending ones, and a cleared entry would report them PENDING again.
        # Growth is bounded by _remember_operation.
        return {
            "operation": {
                "name": operation_id,
                "metadata": {"video": {"mediaId": media_id, "fifeUrl": urls.video}},
            },
            "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
        }

    async def _find_operation_media(self, operation_id: str) -> tuple[str | None, str | None]:
        """Ask the operation how it is going, then the listing where its media is.

        The listing is the authority — the poll has been seen to never report a
        finished job the listing already knows about — but it is also the
        expensive call, so it is only consulted when the poll says something
        happened, when the poll is unreadable, or every third round regardless.
        """
        rounds = self._operation_polls.get(operation_id, 0) + 1
        self._operation_polls[operation_id] = rounds

        project_id = self._operation_projects.get(operation_id) or FLOW_PROJECT_ID
        complaint = None
        worth_looking = rounds % 3 == 0
        try:
            operation = fb.read_operation(
                await self._batch_payload(
                    fb.RPC_OPERATION, fb.operation_request(operation_id), timeout=60)
            )
            complaint = operation.error
            project_id = operation.project_id or project_id
            if project_id:
                self._remember_operation(operation_id, project_id)
            worth_looking = worth_looking or operation.done or operation.complained
        except Exception as e:
            # An operation that has decayed to a bare id still shows up in the
            # listing, so a failed poll is a reason to look there, not to stop.
            logger.debug("Operation %s poll unreadable (%s), trying the listing",
                         operation_id[:20], e)
            worth_looking = True

        if not worth_looking:
            return None, complaint
        if not project_id:
            return None, "no project id for the listing lookup"
        return await self._media_id_for(operation_id, project_id), complaint

    async def _media_id_for(self, operation_id: str, project_id: str) -> str | None:
        """Find an operation's media id in the project listing.

        Asks the extension for an 800-byte window around the operation id
        rather than the whole listing — that payload is past 17 MB and grows
        with every generation, so anything that ships it whole gets truncated
        and loses roughly half of all lookups.
        """
        result = await self.batch_rpc(
            fb.RPC_PROJECT_MEDIA, fb.project_media_request(project_id),
            match=operation_id, timeout=120,
        )
        if result.get("error"):
            raise fb.FlowBatchError(f"{fb.RPC_PROJECT_MEDIA}: {result['error']}")
        raw = result.get("data") or ""
        media_id = fb.find_media_id_in_text(raw, operation_id)
        if not media_id and raw.lstrip().startswith(")]}"):
            # an extension that cannot filter hands back the whole envelope
            try:
                media_id = fb.find_media_id(
                    fb.first_payload(raw, fb.RPC_PROJECT_MEDIA), operation_id)
            except (fb.FlowBatchError, fb.RpcError, json.JSONDecodeError):
                media_id = None
        return media_id

    async def _batch_media_urls(self, media_id: str) -> "fb.MediaUrls":
        payload = await self._batch_payload(
            fb.RPC_MEDIA, fb.media_request(media_id), timeout=60)
        return fb.read_media_urls(payload, media_id)

    async def resolve_media_url(self, media_id: str, timeout: float = 60) -> str | None:
        """Resolve a media id to a fresh signed Flow CDN URL."""
        if not media_id:
            return None
        urls = await asyncio.wait_for(self._batch_media_urls(media_id), timeout=timeout)
        return urls.video or urls.image

    async def resolve_media_urls(self, media_ids: list[str]) -> dict[str, str]:
        """Resolve multiple media ids, limiting concurrent RPCs to five."""
        unique_ids = list(dict.fromkeys(mid for mid in (media_ids or []) if mid))
        if not unique_ids:
            return {}
        semaphore = asyncio.Semaphore(5)

        async def resolve_one(media_id: str):
            async with semaphore:
                return await self.resolve_media_url(media_id, timeout=60)

        resolved = await asyncio.gather(
            *(resolve_one(media_id) for media_id in unique_ids),
            return_exceptions=True,
        )
        output: dict[str, str] = {}
        for media_id, value in zip(unique_ids, resolved):
            if isinstance(value, Exception):
                logger.warning("Media %s resolve failed: %s", media_id[:12], value)
            elif isinstance(value, str) and value:
                output[media_id] = value
        return output

    async def get_credits(self) -> dict:
        """Get user credits and tier.

        The new frontend has no captured credits rpc, and the tier no longer
        selects a model — aspect is its own slot and the model names are
        fixed — so on the batch path this answers with the configured default
        rather than pretending to know.
        """
        return {"status": 200, "data": {
            "userPaygateTier": DEFAULT_PAYGATE_TIER,
            "note": "batchexecute path: tier is configured (DEFAULT_PAYGATE_TIER), not fetched",
        }}

    async def validate_media_id(self, media_id: str) -> bool:
        """Check if a mediaId is still valid."""
        result = await self.get_media(media_id)
        status = result.get("status", 500)
        return isinstance(status, int) and status == 200

    async def get_media(self, media_id: str) -> dict:
        """Fetch a media record, which is where a fresh signed url lives."""
        try:
            urls = await self._batch_media_urls(media_id)
        except Exception as e:
            return _batch_error(e)
        if not urls.video and not urls.image:
            return {"status": 404, "error": f"No urls for media {media_id}"}
        data: dict = {}
        if urls.video:
            data["video"] = {"fifeUrl": urls.video}
        if urls.image:
            data["image"] = {"fifeUrl": urls.image}
        return {"status": 200, "data": data}

    async def upload_image(self, image_base64: str, mime_type: str = "image/jpeg",
                            project_id: str = "", file_name: str = "image.jpg") -> dict:
        """Upload an image into the project so it can be used as a reference."""
        try:
            pid = self._batch_project_id(project_id)
            payload = await self._batch_payload(
                fb.RPC_UPLOAD_IMAGE,
                fb.upload_request(image_base64, pid, mime_type, file_name),
                fb.CAPTCHA_IMAGE, timeout=120,
            )
            media_id = fb.read_uploaded_media_id(payload)
        except Exception as e:
            return _batch_error(e)
        return {"status": 200, "data": {"media": {"name": media_id}}, "_mediaId": media_id}

# ─── Response shaping ────────────────────────────────────────
#
# The batch path answers in Flow's positional arrays; everything downstream
# reads the old REST shapes. These put one back on the other so the parsers,
# the poller and the DB writers never learn which transport ran.

_CAPTURE_HINT = "see docs/CAPTURE.md to record its payload off the new UI"

_UNSUPPORTED_CREATE_PROJECT = (
    "NO_FLOW_PROJECT: Flow's project.createProject endpoint went with the September 2026 "
    "migration, so Flow Kit cannot create one. Make a project in the Flow UI, then either "
    "pass its uuid as flow_project_id or pin it as FLOW_PROJECT_ID."
)


def _unsupported(feature: str, why: str) -> str:
    return f"UNSUPPORTED_ON_BATCH_API: {feature} — {why}; {_CAPTURE_HINT}."


def _batch_error(exc: Exception) -> dict:
    """An exception from the batch path, in the error shape callers expect."""
    return {"status": 502, "error": f"{type(exc).__name__}: {exc}"}


def _as_media_record(image: "fb.GeneratedImage") -> dict:
    """One generated image, in the REST response's `media[]` shape."""
    return {
        "name": image.media_id,
        "image": {"generatedImage": {"mediaId": image.media_id, "fifeUrl": image.url}},
    }


def _as_pending_operation(operation_id: str, error: str | None = None,
                          media_id: str | None = None) -> dict:
    """An operation that has not produced a fetchable clip yet.

    ``error`` is carried, not acted on: a poll complaint is a diagnostic that
    finished jobs also report, so it exists to make a timeout message useful.
    """
    entry: dict = {
        "operation": {"name": operation_id},
        "status": "MEDIA_GENERATION_STATUS_PENDING",
    }
    if media_id:
        entry["operation"]["metadata"] = {"video": {"mediaId": media_id}}
    if error:
        entry["complaint"] = error
    return entry



def _is_ws_error(result: dict) -> bool:
    return bool(result.get("error")) or (isinstance(result.get("status"), int) and result["status"] >= 400)


# Singleton
_client: Optional[FlowClient] = None


def get_flow_client() -> FlowClient:
    global _client
    if _client is None:
        _client = FlowClient()
    return _client
