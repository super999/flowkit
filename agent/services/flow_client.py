"""
Flow Client — communicates with Google Flow API via Chrome extension WebSocket bridge.

Agent runs a WS server. Extension connects as client. Agent sends API requests,
extension executes them in browser context (residential IP, cookies, reCAPTCHA).
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.config import (
    GOOGLE_FLOW_API, GOOGLE_API_KEY, ENDPOINTS,
    VIDEO_MODELS, UPSCALE_MODELS, IMAGE_MODELS, VIDEO_POLL_TIMEOUT,
)
from agent.services.headers import random_headers

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


def extract_prompt_pair_from_media(item: dict, wf: dict = None) -> tuple[str, Optional[str]]:
    """Extract (original_prompt, translated_prompt) from Flow media and workflow.

    Returns:
        (original_prompt, translated_prompt):
        - original_prompt: user's original input (e.g. Chinese) or primary prompt
        - translated_prompt: translated English prompt if different, else None
    """
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
                if parts and isinstance(parts, list):
                    texts = [p.get("text", "").strip() for p in parts if isinstance(p, dict) and p.get("text")]
                    if texts:
                        orig_prompt = "".join(texts).strip()
            trans_prompt = pi.get("textInput", "").strip()

    if not orig_prompt:
        media_title = meta.get("mediaTitle", "").strip() if isinstance(meta, dict) else ""
        if media_title:
            orig_prompt = media_title
        elif wf and isinstance(wf, dict):
            wf_meta = wf.get("metadata", {}) or wf.get("mediaMetadata", {}) if isinstance(wf, dict) else {}
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
    elif cleaned_orig:
        return cleaned_orig, None
    elif cleaned_trans:
        return cleaned_trans, None
    elif model_prompt:
        cleaned_model = clean_flow_prompt_text(model_prompt)
        return cleaned_model, None
    return "", None


class FlowClient:
    """Sends commands to Chrome extension via WebSocket."""

    def __init__(self):
        self._extension_ws = None  # Active authenticated extension connection
        self._extensions: dict[object, dict] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_ws: dict[str, object] = {}
        self._flow_key: Optional[str] = None
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
        return {
            "connected": self.connected,
            "active_connections": len(self._extensions),
            "authenticated_connections": sum(
                1 for session in self._extensions.values()
                if session.get("flow_key")
            ),
            "connects": self._ws_connect_count,
            "disconnects": self._ws_disconnect_count,
            "uptime_s": uptime,
        }

    async def _handle_page_scan(self, detail: dict):
        """Resolve media ids reported by the page scan into real URLs (flow_media)."""
        from agent.db import crud

        media_ids = detail.get("mediaIds") or []
        if not media_ids:
            return
        try:
            existing = {r["media_id"] for r in await crud.list_flow_media()}
        except Exception:
            existing = set()

        todo = [mid for mid in media_ids if mid not in existing]
        if not todo:
            logger.info("Page scan: all %d media already in library", len(media_ids))
            return

        logger.info("Page scan: resolving %d new media ids → flow_media", len(todo))
        # Resolve in small batches to be gentle on the extension
        import asyncio as _asyncio
        for i in range(0, len(todo), 4):
            batch = todo[i:i + 4]
            results = await _asyncio.gather(*[
                self.resolve_media_url(mid) for mid in batch
            ], return_exceptions=True)
            for mid, url in zip(batch, results):
                if isinstance(url, Exception) or not url:
                    logger.warning("Media %s resolve failed: %s", mid[:12], url if isinstance(url, Exception) else "no final URL")
                    continue
                try:
                    media_type = "video" if "/video/" in url else "image"
                    await crud.upsert_flow_media(mid, media_type, url)
                    logger.info("Media %s → %s", mid[:12], url[:60])
                except Exception as e:
                    logger.warning("flow_media upsert failed for %s: %s", mid[:12], e)
            await _asyncio.sleep(1.0)
        await self._after_media_sync(len(todo))

    async def _after_media_sync(self, count: int):
        try:
            from agent.services.event_bus import event_bus
            await event_bus.emit("media_synced", {"count": count})
        except Exception:
            pass

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
            logger.info("Extension ready, flowKey=%s", "yes" if data.get("flowKeyPresent") else "no")
            asyncio.create_task(self._sync_tier())
            return

        if data.get("type") == "media_urls_refresh":
            asyncio.create_task(self._refresh_media_urls(data.get("urls", [])))
            return

        if data.get("type") == "page_scan_report":
            # Debug diagnostics + media ids from the extension's DOM media scan
            try:
                capture_dir = Path(__file__).parent.parent.parent / "output" / "_shared"
                capture_dir.mkdir(parents=True, exist_ok=True)
                cap_file = capture_dir / "page_scan_reports.jsonl"
                with open(cap_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": datetime.now().isoformat(),
                        **{k: v for k, v in (data.get("detail") or {}).items() if k != "mediaIds"},
                        "mediaIds": (data.get("detail") or {}).get("mediaIds", []),
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass
            # Resolve the reported media ids into real URLs
            asyncio.create_task(self._handle_page_scan(data.get("detail") or {}))
            return

        if data.get("type") == "api_request_capture":
            # Raw request payload captured from the Flow web UI (debugging tool)
            try:
                url = data.get("url", "")
                if "upsampleImage" not in url and "batchGenerateImages" not in url and "/api/trpc/" not in url:
                    return
                capture_dir = Path(__file__).parent.parent.parent / "output" / "_shared"
                capture_dir.mkdir(parents=True, exist_ok=True)
                cap_file = capture_dir / "captured_api_requests.jsonl"
                with open(cap_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": datetime.now().isoformat(),
                        "url": url,
                        "body": data.get("body", ""),
                    }, ensure_ascii=False) + "\n")
                logger.info("Captured Flow web API request → output/_shared/captured_api_requests.jsonl")
            except Exception as e:
                logger.warning("Failed to save captured API request: %s", e)
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
    _SAFE_URL_RE = __import__("re").compile(r'^https://(storage\.googleapis\.com|lh3\.googleusercontent\.com|flow-content\.google)/')

    async def _refresh_media_urls(self, urls: list[dict]):
        """Update scene/character URLs in DB from fresh TRPC-captured signed URLs.

        Each entry: {mediaId: str, mediaType: 'image'|'video', url: str}
        Also stores every entry into flow_media (powers the project media library).
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

            # Record into the flow_media library (all project media)
            try:
                await crud.upsert_flow_media(media_id, media_type, url)
            except Exception as e:
                logger.warning("flow_media upsert failed for %s: %s", media_id[:12], e)

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
        """Refresh media URLs for a project.

        Note: Google Flow's get_media API returns encoded content (base64),
        not fresh signed URLs. URL refresh requires TRPC intercept from
        the extension when the user opens the project in Chrome.
        The video reviewer falls back to get_media content directly.
        """
        logger.info("URL refresh requested for project %s — TRPC endpoint no longer available, "
                     "use extension passive intercept (open project in Chrome)", project_id[:12])
        return {"refreshed": 0, "found": 0, "note": "TRPC endpoint unavailable. "
                "Video reviewer uses get_media fallback automatically. "
                "For URL refresh, open the project in Google Flow in Chrome."}

    async def _send(self, method: str, params: dict, timeout: float = 300) -> dict:
        """Send request to extension and wait for response.

        Always returns a dict. On error, returns {"error": "<reason>"} — callers
        must check result.get("error") or use _is_ws_error() before reading data.
        Never raises; exceptions are caught and returned as error dicts.
        """
        if not self.connected:
            return {"error": "Extension not connected"}

        extension_candidates = self._extension_candidates(require_token=True)
        if not extension_candidates:
            return {"error": "NO_FLOW_KEY"}

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

    def _build_url(self, endpoint_key: str, **kwargs) -> str:
        """Build full API URL."""
        path = ENDPOINTS[endpoint_key].format(**kwargs)
        sep = "&" if "?" in path else "?"
        return f"{GOOGLE_FLOW_API}{path}{sep}key={GOOGLE_API_KEY}"

    def _client_context(self, project_id: str, user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Build clientContext with recaptcha placeholder."""
        return {
            "projectId": str(project_id),
            "recaptchaContext": {
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                "token": "",  # Extension injects real token
            },
            "sessionId": f";{int(time.time() * 1000)}",
            "tool": "PINHOLE",
            "userPaygateTier": user_paygate_tier,
        }

    # ─── High-level API Methods ──────────────────────────────

    async def create_project(self, project_title: str, tool_name: str = "PINHOLE") -> dict:
        """Create a project on Google Flow via tRPC endpoint.

        Returns the full response including projectId.
        """
        url = "https://labs.google/fx/api/trpc/project.createProject"
        body = {"json": {"projectTitle": project_title, "toolName": tool_name}}

        return await self._send("trpc_request", {
            "url": url,
            "method": "POST",
            "headers": {
                "content-type": "application/json",
                "accept": "*/*",
            },
            "body": body,
        }, timeout=30)

    def _extract_trpc_result(self, result: dict) -> dict:
        """Safely extract json.result dict from tRPC response (handles list and dict shapes)."""
        if not isinstance(result, dict):
            return {}
        raw_data = result.get("data")
        if isinstance(raw_data, list) and raw_data:
            item = raw_data[0]
        elif isinstance(raw_data, dict):
            item = raw_data
        else:
            return {}
        try:
            json_data = item.get("result", {}).get("data", {}).get("json", {})
            if isinstance(json_data.get("result"), dict):
                return json_data["result"]
            return json_data if isinstance(json_data, dict) else {}
        except (KeyError, TypeError, AttributeError):
            return {}

    async def search_user_projects(self, limit: int = 50,
                                    tool_name: str = "PINHOLE") -> list[dict]:
        """List user projects from Google Flow with nextCursor pagination support."""
        import urllib.parse

        all_projects = []
        cursor = None

        while len(all_projects) < limit:
            chunk_size = min(20, limit - len(all_projects))
            input_dict = {"toolName": tool_name, "pageSize": chunk_size}
            if cursor:
                input_dict["cursor"] = cursor

            input_data = {"json": input_dict}
            encoded = urllib.parse.quote(json.dumps(input_data))
            url = f"https://labs.google/fx/api/trpc/project.searchUserProjects?input={encoded}"

            result = await self._send("trpc_request", {
                "url": url,
                "method": "GET",
                "headers": {"content-type": "application/json"},
            }, timeout=30)

            res_json = self._extract_trpc_result(result)
            projects = res_json.get("projects", [])
            if not projects:
                break
            all_projects.extend(projects)

            next_cursor = res_json.get("nextCursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return all_projects

    async def get_project(self, project_id: str,
                           tool_name: str = "PINHOLE") -> dict:
        """Get project metadata from Google Flow."""
        import urllib.parse
        input_data = {"json": {"projectId": project_id, "toolName": tool_name}}
        encoded = urllib.parse.quote(json.dumps(input_data))
        url = f"https://labs.google/fx/api/trpc/project.getProject?input={encoded}"

        result = await self._send("trpc_request", {
            "url": url,
            "method": "GET",
            "headers": {"content-type": "application/json"},
        }, timeout=30)

        return self._extract_trpc_result(result)

    async def fetch_user_history(self, limit: int = 20,
                                  history_type: str = "FLOW",
                                  tool_name: str = "PINHOLE",
                                  page_token: str = None) -> list[dict]:
        """Fetch user media history from Google Flow. Supports pageToken pagination."""
        import urllib.parse

        all_workflows = []
        current_token = page_token
        max_fetch = min(limit, 200) # Support up to 200 items (10 pages)

        while len(all_workflows) < max_fetch:
            chunk_size = min(20, max_fetch - len(all_workflows))
            inp_data = {
                "toolName": tool_name,
                "limit": chunk_size,
                "type": history_type,
            }
            if current_token:
                inp_data["pageToken"] = current_token

            encoded = urllib.parse.quote(json.dumps({"json": inp_data}))
            url = f"https://labs.google/fx/api/trpc/media.fetchUserHistory?input={encoded}"

            result = await self._send("trpc_request", {
                "url": url,
                "method": "GET",
                "headers": {"content-type": "application/json"},
            }, timeout=60)

            res_data = self._extract_trpc_result(result)
            workflows = res_data.get("userWorkflows", [])
            if not workflows:
                break
            all_workflows.extend(workflows)

            next_token = res_data.get("nextPageToken")
            # Stop if no next token or if token didn't change (end of pages)
            if not next_token or next_token == current_token:
                break
            current_token = next_token

            # Single page request exit
            if page_token is None and limit <= 20:
                break

        return all_workflows

    async def get_project_initial_data(self, project_id: str) -> dict:
        """Fetch project initial data including projectContents (workflows & media)."""
        import urllib.parse
        input_data = {"json": {"projectId": project_id}}
        encoded = urllib.parse.quote(json.dumps(input_data))
        url = f"https://labs.google/fx/api/trpc/flow.projectInitialData?input={encoded}"

        result = await self._send("trpc_request", {
            "url": url,
            "method": "GET",
            "headers": {"content-type": "application/json"},
        }, timeout=30)

        res_data = self._extract_trpc_result(result)
        return res_data.get("projectContents", {})

    async def fetch_project_media(self, project_id: str,
                                    limit: int = 150) -> list[dict]:
        """Fetch all media for a specific project.

        1. Primary strategy: Use newly discovered `flow.projectInitialData` for 100% accurate,
           project-specific media retrieval.
        2. Fallback strategy: Multi-page `fetch_user_history` filtered by projectId.
        """
        from agent.db import crud

        results = []
        seen_keys = set()

        # 1. Primary Strategy: Try flow.projectInitialData tRPC endpoint first
        p_contents = await self.get_project_initial_data(project_id)
        raw_media = p_contents.get("media", [])
        raw_workflows = p_contents.get("workflows", [])

        # Build workflow lookup map by workflowId
        wf_map = {}
        for wf in raw_workflows:
            w_id = wf.get("workflowId") or wf.get("id") or wf.get("name")
            if w_id:
                wf_map[w_id] = wf

        if raw_media:
            for item in raw_media:
                # mediaKey can be in item.name or mediaGenerationId.mediaKey
                gen_id = item.get("mediaGenerationId", {})
                media_key = gen_id.get("mediaKey") or item.get("name", "")
                if not media_key or media_key in seen_keys:
                    continue
                seen_keys.add(media_key)

                w_id = gen_id.get("workflowId", "") or item.get("workflowId", "")
                wf = wf_map.get(w_id, {})

                img = item.get("image", {}) or wf.get("image", {})
                vid = item.get("video", {}) or wf.get("video", {})
                gen_img = img.get("generatedImage", {}) if isinstance(img, dict) else {}
                gen_vid = vid.get("generatedVideo", {}) if isinstance(vid, dict) else {}

                orig_prompt, trans_prompt = extract_prompt_pair_from_media(item, wf)

                model = (
                    gen_img.get("modelNameType", "") or gen_vid.get("modelNameType", "") or
                    img.get("modelNameType", "") or vid.get("modelNameType", "") or
                    wf.get("modelName", "") or wf.get("model", "")
                )
                aspect = (
                    gen_img.get("aspectRatio", "") or gen_vid.get("aspectRatio", "") or
                    img.get("aspectRatio", "") or vid.get("aspectRatio", "") or
                    wf.get("aspectRatio", "")
                )
                meta = item.get("mediaMetadata", {}) or item.get("metadata", {}) if isinstance(item, dict) else {}
                wf_meta = wf.get("metadata", {}) or wf.get("mediaMetadata", {}) if isinstance(wf, dict) else {}
                create_time = (
                    item.get("createTime", "") or item.get("creationTime", "") or item.get("createdAt", "") or
                    meta.get("createTime", "") or meta.get("creationTime", "") or meta.get("createdAt", "") or
                    wf.get("createTime", "") or wf.get("creationTime", "") or wf.get("createdAt", "") or
                    wf_meta.get("createTime", "") or wf_meta.get("creationTime", "") or wf_meta.get("createdAt", "")
                )
                fife_url = (
                    gen_img.get("fifeUrl") or gen_img.get("servingUri") or
                    img.get("fifeUrl") or img.get("servingUri") or
                    gen_vid.get("downloadUrl") or gen_vid.get("fifeUrl") or
                    vid.get("downloadUrl") or vid.get("fifeUrl") or
                    item.get("fifeUrl") or wf.get("fifeUrl") or ""
                )

                results.append({
                    "mediaKey": media_key,
                    "mediaType": gen_id.get("mediaType") or ("VIDEO" if vid else "IMAGE"),
                    "prompt": orig_prompt,
                    "translated_prompt": trans_prompt,
                    "modelName": model,
                    "aspectRatio": aspect,
                    "workflowId": w_id,
                    "createTime": create_time,
                    "url": fife_url,
                })

        # 2. Fallback Strategy: If projectInitialData returned nothing, query fetch_user_history
        if not results:
            workflows = await self.fetch_user_history(limit=limit)
            for wf in workflows:
                media = wf.get("media", {})
                gen_id = media.get("mediaGenerationId", {})
                if gen_id.get("projectId") != project_id:
                    continue

                media_key = gen_id.get("mediaKey", "")
                if not media_key or media_key in seen_keys:
                    continue
                seen_keys.add(media_key)

                img = media.get("image", {})
                vid = media.get("video", {})
                gen_img = img.get("generatedImage", {}) if isinstance(img, dict) else {}
                gen_vid = vid.get("generatedVideo", {}) if isinstance(vid, dict) else {}

                orig_prompt, trans_prompt = extract_prompt_pair_from_media(media, wf)

                model = gen_img.get("modelNameType", "") or gen_vid.get("modelNameType", "") or img.get("modelNameType", "") or vid.get("modelNameType", "")
                aspect = gen_img.get("aspectRatio", "") or gen_vid.get("aspectRatio", "") or img.get("aspectRatio", "") or vid.get("aspectRatio", "")

                wf_meta = wf.get("metadata", {}) or wf.get("mediaMetadata", {}) if isinstance(wf, dict) else {}
                create_time = (
                    wf.get("createTime", "") or wf.get("creationTime", "") or wf.get("createdAt", "") or
                    wf_meta.get("createTime", "") or wf_meta.get("creationTime", "") or wf_meta.get("createdAt", "")
                )
                fife_url = (
                    gen_img.get("fifeUrl") or gen_img.get("servingUri") or
                    img.get("fifeUrl") or img.get("servingUri") or
                    gen_vid.get("downloadUrl") or gen_vid.get("fifeUrl") or
                    vid.get("downloadUrl") or vid.get("fifeUrl") or ""
                )

                results.append({
                    "mediaKey": media_key,
                    "mediaType": gen_id.get("mediaType", "IMAGE"),
                    "prompt": orig_prompt,
                    "translated_prompt": trans_prompt,
                    "modelName": model,
                    "aspectRatio": aspect,
                    "workflowId": gen_id.get("workflowId", ""),
                    "createTime": create_time,
                    "url": fife_url,
                })

        # 3. Merge local flow_media DB entries ONLY IF they explicitly belong to this project
        try:
            db_rows = await crud.list_flow_media(limit=500)
            for row in db_rows:
                row_pid = row.get("project_id", "")
                if row_pid and row_pid != project_id:
                    continue

                mk = row.get("media_id", "")
                if mk and mk not in seen_keys and row_pid == project_id:
                    seen_keys.add(mk)
                    results.append({
                        "mediaKey": mk,
                        "mediaType": row.get("media_type", "IMAGE"),
                        "prompt": row.get("prompt", ""),
                        "modelName": row.get("model_name", ""),
                        "aspectRatio": row.get("aspect_ratio", ""),
                        "workflowId": "",
                        "createTime": row.get("created_at", "") or row.get("updated_at", ""),
                        "url": row.get("url", ""),
                    })
        except Exception as e:
            logger.warning("fetch_project_media: failed to query local flow_media: %s", e)

        # Sort results chronologically descending (newest first), matching Google Flow's layout
        results.sort(key=lambda x: x.get("createTime") or "", reverse=True)

        return results

    async def resolve_media_url(self, media_id: str, timeout: int = 15) -> str:
        """Resolve a mediaKey/media_id to its signed CDN URL via extension or Google API."""
        if not media_id:
            return ""

        from urllib.parse import quote

        # 1. Try trpc_request with responseMode="url" via extension background
        try:
            url = f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={quote(media_id, safe='')}"
            res = await self._send("trpc_request", {
                "url": url,
                "method": "GET",
                "headers": {"content-type": "application/json"},
                "responseMode": "url",
            }, timeout=timeout)
            logger.info("resolve_media_url trpc_request raw for %s: %s", media_id, res)
            if isinstance(res, dict):
                data = res.get("data", {})
                final_url = data.get("url", "") if isinstance(data, dict) else ""
                status = res.get("status", 200)
                logger.info("resolve_media_url trpc_request parsed: status=%s, final_url=%s", status, final_url)
                if status == 200 and final_url and (
                    final_url.startswith("https://flow-content.google") or
                    "storage.googleapis.com" in final_url or
                    "googleusercontent.com" in final_url
                ):
                    return final_url
        except Exception as e:
            logger.warning("resolve_media_url trpc_request failed for %s: %s", media_id, e)

        # 2. Fallback: Try fetch_redirect via extension
        try:
            url = f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={quote(media_id, safe='')}"
            res = await self._send("fetch_redirect", {"url": url}, timeout=timeout)
            logger.info("resolve_media_url fetch_redirect raw for %s: %s", media_id, res)
            if isinstance(res, dict):
                final_url = res.get("finalUrl", "")
                status = res.get("status", 200)
                logger.info("resolve_media_url fetch_redirect parsed: status=%s, final_url=%s", status, final_url)
                if status == 200 and final_url and (
                    final_url.startswith("https://flow-content.google") or
                    "storage.googleapis.com" in final_url or
                    "googleusercontent.com" in final_url
                ):
                    return final_url
        except Exception as e:
            logger.warning("resolve_media_url fetch_redirect failed for %s: %s", media_id, e)

        return ""

    async def resolve_media_urls(self, media_ids: list[str]) -> dict[str, str]:
        """Batch resolve multiple mediaKeys/media_ids to signed CDN URLs."""
        tasks = [self.resolve_media_url(mid) for mid in media_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        resolved = {}
        for mid, res in zip(media_ids, results):
            if isinstance(res, str) and res:
                resolved[mid] = res
        return resolved

    async def generate_images(self, prompt: str, project_id: str,
                               aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
                               user_paygate_tier: str = "PAYGATE_TIER_TWO",
                               character_media_ids: list[str] = None,
                               image_model: str = None,
                               source_media_id: str = None) -> dict:
        """Generate image(s).

        If character_media_ids is provided, uses edit_image flow (batchGenerateImages
        with imageInputs) — same endpoint, but includes character references.
        Without characters, uses plain generate_images.

        image_model overrides the model (e.g. GEM_PIX_2_UPSAMPLE_2K / _4K for
        resolution upsampling). source_media_id adds the source as BASE_IMAGE input.

        Response structure:
            data.media[].name = mediaId (used for video gen)
        """
        ts = int(time.time() * 1000)
        ctx = self._client_context(project_id, user_paygate_tier)

        request_item = {
            "clientContext": {**ctx, "sessionId": f";{ts}"},
            "seed": ts % 1000000,
            "structuredPrompt": {"parts": [{"text": prompt}]},
            "imageAspectRatio": aspect_ratio,
            "imageModelName": image_model or IMAGE_MODELS["NANO_BANANA_PRO"],
        }

        # Add character references if provided (edit_image flow)
        if character_media_ids:
            request_item["imageInputs"] = [
                {"name": mid, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
                for mid in character_media_ids
            ]
        if source_media_id:
            request_item["imageInputs"] = [
                {"name": source_media_id, "imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE"},
                *(request_item.get("imageInputs") or []),
            ]

        batch_id = f"{uuid.uuid4()}" if character_media_ids else None
        body = {
            "clientContext": ctx,
            "requests": [request_item],
        }
        if batch_id:
            body["mediaGenerationContext"] = {"batchId": batch_id}
            body["useNewMedia"] = True

        url = self._build_url("generate_images", project_id=project_id)
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "IMAGE_GENERATION",
        })

    async def edit_image(self, prompt: str, source_media_id: str,
                          project_id: str,
                          aspect_ratio: str = "IMAGE_ASPECT_RATIO_PORTRAIT",
                          user_paygate_tier: str = "PAYGATE_TIER_ONE",
                          character_media_ids: list[str] = None) -> dict:
        """Edit an existing image using IMAGE_INPUT_TYPE_BASE_IMAGE.

        If character_media_ids is provided, appends them as IMAGE_INPUT_TYPE_REFERENCE
        after the base image. Order: [base_image, char_A, char_B, ...].
        This helps Google Flow detect characters for consistent edits.
        """
        ts = int(time.time() * 1000)
        ctx = self._client_context(project_id, user_paygate_tier)

        image_inputs = [
            {"name": source_media_id, "imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE"}
        ]
        if character_media_ids:
            for mid in character_media_ids:
                image_inputs.append({"name": mid, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"})

        request_item = {
            "clientContext": {**ctx, "sessionId": f";{ts}"},
            "seed": ts % 1000000,
            "structuredPrompt": {"parts": [{"text": prompt}]},
            "imageAspectRatio": aspect_ratio,
            "imageModelName": IMAGE_MODELS["NANO_BANANA_PRO"],
            "imageInputs": image_inputs,
        }

        body = {
            "clientContext": ctx,
            "mediaGenerationContext": {"batchId": f"{uuid.uuid4()}"},
            "useNewMedia": True,
            "requests": [request_item],
        }

        url = self._build_url("generate_images", project_id=project_id)
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "IMAGE_GENERATION",
        })

    async def upscale_image(self, media_id: str, project_id: str = "",
                            target_resolution: str = "UPSAMPLE_IMAGE_RESOLUTION_4K",
                            user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Upscale an existing image to 2K/4K via /v1/flow/upsampleImage.

        Exact request format captured from Flow web UI:
            {mediaId, targetResolution, clientContext{recaptchaContext, projectId, tool, userPaygateTier, sessionId}}
        Response contains the upscaled media (new mediaId + fifeUrl).
        """
        ts = int(time.time() * 1000)
        body = {
            "mediaId": media_id,
            "targetResolution": target_resolution,
            "clientContext": {
                "recaptchaContext": {
                    "token": "",
                    "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                },
                "projectId": str(project_id),
                "tool": "PINHOLE",
                "userPaygateTier": user_paygate_tier,
                "sessionId": f";{ts}",
            },
        }
        url = self._build_url("upscale_image")
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "IMAGE_GENERATION",
        }, timeout=300)

    async def generate_video(self, start_image_media_id: str, prompt: str,
                              project_id: str, scene_id: str,
                              aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                              end_image_media_id: str = None,
                              user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Generate video from start image (i2v).

        Two sub-types:
        - frame_2_video (i2v): startImage only
        - start_end_frame_2_video (i2v_fl): startImage + endImage (for scene chaining)
        """
        gen_type = "start_end_frame_2_video" if end_image_media_id else "frame_2_video"
        model_key = VIDEO_MODELS.get(user_paygate_tier, {}).get(gen_type, {}).get(aspect_ratio)

        if not model_key:
            return {"error": f"No model for tier={user_paygate_tier} type={gen_type} ratio={aspect_ratio}"}

        request = {
            "aspectRatio": aspect_ratio,
            "seed": int(time.time()) % 10000,
            "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
            "videoModelKey": model_key,
            "startImage": {"mediaId": start_image_media_id},
            "metadata": {"sceneId": scene_id},
        }

        if end_image_media_id:
            request["endImage"] = {"mediaId": end_image_media_id}

        endpoint_key = "generate_video_start_end" if end_image_media_id else "generate_video"
        body = {
            "mediaGenerationContext": {"batchId": f"{uuid.uuid4()}"},
            "clientContext": self._client_context(project_id, user_paygate_tier),
            "requests": [request],
            "useV2ModelConfig": True,
        }

        url = self._build_url(endpoint_key)
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "VIDEO_GENERATION",
        }, timeout=60)  # Submit only — polling is separate

    async def generate_video_from_references(self, reference_media_ids: list[str],
                                              prompt: str, project_id: str, scene_id: str,
                                              aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                                              user_paygate_tier: str = "PAYGATE_TIER_TWO") -> dict:
        """Generate video from multiple reference images (r2v).

        Uses referenceImages instead of startImage — the model composes
        a video from all provided reference character images.

        Args:
            reference_media_ids: List of character media_ids (from uploadImage)
        """
        gen_type = "reference_frame_2_video"
        model_key = VIDEO_MODELS.get(user_paygate_tier, {}).get(gen_type, {}).get(aspect_ratio)

        if not model_key:
            return {"error": f"No model for tier={user_paygate_tier} type={gen_type} ratio={aspect_ratio}"}

        request = {
            "aspectRatio": aspect_ratio,
            "seed": int(time.time()) % 10000,
            "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
            "videoModelKey": model_key,
            "referenceImages": [
                {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                for mid in reference_media_ids
            ],
            "metadata": {},
        }

        body = {
            "mediaGenerationContext": {"batchId": f"{uuid.uuid4()}"},
            "clientContext": self._client_context(project_id, user_paygate_tier),
            "requests": [request],
            "useV2ModelConfig": True,
        }

        url = self._build_url("generate_video_references")
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "VIDEO_GENERATION",
        }, timeout=60)

    async def upscale_video(self, media_id: str, scene_id: str,
                             aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
                             resolution: str = "VIDEO_RESOLUTION_4K") -> dict:
        """Upscale a video."""
        model_key = UPSCALE_MODELS.get(resolution, "veo_3_1_upsampler_4k")

        body = {
            "clientContext": {
                "sessionId": f";{int(time.time() * 1000)}",
                "recaptchaContext": {
                    "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                    "token": "",
                },
            },
            "requests": [{
                "aspectRatio": aspect_ratio,
                "resolution": resolution,
                "seed": int(time.time()) % 100000,
                "metadata": {"sceneId": scene_id},
                "videoInput": {"mediaId": media_id},
                "videoModelKey": model_key,
            }],
        }

        url = self._build_url("upscale_video")
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
            "captchaAction": "VIDEO_GENERATION",
        }, timeout=60)

    async def check_video_status(self, operations: list[dict]) -> dict:
        """Check status of video generation operations."""
        body = {"operations": operations}
        url = self._build_url("check_video_status")
        return await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
        }, timeout=30)  # No captcha needed

    async def get_credits(self) -> dict:
        """Get user credits and tier."""
        url = self._build_url("get_credits")
        return await self._send("api_request", {
            "url": url,
            "method": "GET",
            "headers": random_headers(),
        }, timeout=15)

    async def validate_media_id(self, media_id: str) -> bool:
        """Check if a mediaId is still valid.

        Production calls: GET /v1/media/{mediaId}?key=...&clientContext.tool=PINHOLE
        Returns True on 200, False otherwise.
        """
        result = await self.get_media(media_id)
        status = result.get("status", 500)
        return isinstance(status, int) and status == 200

    async def get_media(self, media_id: str) -> dict:
        """Fetch media metadata from Google Flow.

        Returns the raw API response which contains a fresh signed URL
        in data.fifeUrl or data.servingUri.
        """
        url = f"{GOOGLE_FLOW_API}/v1/media/{media_id}?key={GOOGLE_API_KEY}&clientContext.tool=PINHOLE"
        return await self._send("api_request", {
            "url": url,
            "method": "GET",
            "headers": random_headers(),
        }, timeout=15)

    async def upload_image(self, image_base64: str, mime_type: str = "image/jpeg",
                            project_id: str = "", file_name: str = "image.jpg") -> dict:
        """Upload an image for use as start/end frame.

        Uses /v1/flow/uploadImage endpoint.
        Response: {media: {name: "uuid", ...}, workflow: {...}}
        We store media.name as the mediaId for video generation.
        """
        body = {
            "clientContext": {
                "projectId": project_id,
                "tool": "PINHOLE",
            },
            "fileName": file_name,
            "imageBytes": image_base64,
            "isHidden": False,
            "isUserUploaded": True,
            "mimeType": mime_type,
        }

        url = self._build_url("upload_image")
        result = await self._send("api_request", {
            "url": url,
            "method": "POST",
            "headers": random_headers(),
            "body": body,
        }, timeout=60)

        # Extract media.name for convenience (used as mediaId in video gen)
        if not _is_ws_error(result):
            data = result.get("data", {})
            if isinstance(data, dict):
                media = data.get("media", {})
                if isinstance(media, dict) and media.get("name"):
                    result["_mediaId"] = media["name"]

        return result


def _is_ws_error(result: dict) -> bool:
    return bool(result.get("error")) or (isinstance(result.get("status"), int) and result["status"] >= 400)


# Singleton
_client: Optional[FlowClient] = None


def get_flow_client() -> FlowClient:
    global _client
    if _client is None:
        _client = FlowClient()
    return _client
