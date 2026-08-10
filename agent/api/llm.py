"""LLM chat test endpoints — call a configured provider and verify availability.

Supports both OpenAI-compatible and Anthropic-compatible APIs.
Provider config comes from settings.json (section "llm").
"""
import logging
import time

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.api.settings import _load as load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

CHAT_TIMEOUT = 60

# Providers whose default protocol is Anthropic-compatible
_ANTHROPIC_DEFAULT = {"anthropic", "minimax"}


class LLMChatMessage(BaseModel):
    role: str = "user"
    content: str


class LLMChatRequest(BaseModel):
    provider: str | None = None  # default: active provider
    messages: list[LLMChatMessage] = Field(default_factory=list)
    max_tokens: int = 512
    system: str | None = None


class LLMChatResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    base_url: str
    reply: str | None = None
    latency_ms: int | None = None
    error: str | None = None


def _resolve_provider_settings(provider: str | None) -> dict:
    llm = load_settings().get("llm") or {}
    providers = llm.get("providers") or {}
    active = provider or llm.get("active_provider")
    if not active:
        raise HTTPException(400, "未配置任何 LLM 供应商，请先到「大语言模型」设置页保存配置")
    cfg = providers.get(active) or {}
    if not cfg.get("api_key"):
        raise HTTPException(400, f"供应商「{active}」未保存 API 密钥，请先到设置页保存")
    if not cfg.get("base_url"):
        raise HTTPException(400, f"供应商「{active}」未保存 API 地址")
    return {"provider": active, **cfg}


def _resolve_api_format(provider: str, base_url: str) -> str:
    """OpenAI-compatible or Anthropic-compatible. Explicit override wins."""
    llm = load_settings().get("llm") or {}
    providers = llm.get("providers") or {}
    cfg = providers.get(provider) or {}
    explicit = cfg.get("api_format")
    if explicit in ("openai", "anthropic"):
        return explicit
    if provider in _ANTHROPIC_DEFAULT or "anthropic" in base_url.lower():
        return "anthropic"
    return "openai"


def _openai_chat_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions" if url.endswith("/v1") else f"{url}/v1/chat/completions"


def _anthropic_chat_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    return f"{url}/v1/messages"


async def _chat_openai(session: aiohttp.ClientSession, cfg: dict, messages: list[dict], max_tokens: int) -> str:
    url = _openai_chat_url(cfg["base_url"])
    payload: dict = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "messages": messages,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=CHAT_TIMEOUT)) as resp:
        body = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {json_dumps(body)}")
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"响应格式异常: {json_dumps(body)}")


async def _chat_anthropic(session: aiohttp.ClientSession, cfg: dict, messages: list[dict], max_tokens: int) -> str:
    url = _anthropic_chat_url(cfg["base_url"])
    # Anthropic API has no system role in `messages`; map to top-level system field
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]
    payload: dict = {
        "model": cfg.get("model") or "claude-sonnet-4-5",
        "max_tokens": max_tokens,
        "messages": user_messages,
    }
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=CHAT_TIMEOUT)) as resp:
        body = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {json_dumps(body)}")
        try:
            return "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise RuntimeError(f"响应格式异常: {json_dumps(body)}")


def json_dumps(obj) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)[:500]
    except Exception:
        return str(obj)[:500]


async def _run_chat(cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """Call the configured provider. Raises RuntimeError on failure."""
    api_format = _resolve_api_format(cfg["provider"], cfg["base_url"])
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        if api_format == "anthropic":
            return await _chat_anthropic(session, cfg, messages, max_tokens)
        return await _chat_openai(session, cfg, messages, max_tokens)


@router.post("/chat", response_model=LLMChatResponse)
async def llm_chat(body: LLMChatRequest):
    """Send a chat message to the configured LLM provider to verify availability."""
    cfg = _resolve_provider_settings(body.provider)
    if not body.messages:
        raise HTTPException(400, "messages 不能为空")

    provider = cfg["provider"]
    api_format = _resolve_api_format(provider, cfg["base_url"])
    messages = [m.model_dump() for m in body.messages]
    if body.system:
        messages.insert(0, {"role": "system", "content": body.system})

    start = time.perf_counter()
    try:
        reply = await _run_chat(cfg, messages, body.max_tokens)
        latency = int((time.perf_counter() - start) * 1000)
        logger.info("LLM chat ok: provider=%s model=%s latency=%dms", provider, cfg.get("model"), latency)
        return LLMChatResponse(
            ok=True,
            provider=provider,
            model=cfg.get("model") or "unknown",
            base_url=cfg["base_url"],
            reply=reply.strip() if reply else "",
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.warning("LLM chat failed: provider=%s err=%s", provider, e)
        return LLMChatResponse(
            ok=False,
            provider=provider,
            model=cfg.get("model") or "unknown",
            base_url=cfg["base_url"],
            error=f"{type(e).__name__}: {e}",
            latency_ms=latency,
        )


# ─── AI 提示词生成 ─────────────────────────────────────────────

class LLMPromptRequest(BaseModel):
    provider: str | None = None  # default: active provider
    mode: str = "random"  # "random" | "keyword"
    keywords: str | None = None
    orientation: str = "VERTICAL"  # "VERTICAL" | "HORIZONTAL"
    language: str = "en"  # "en" | "zh" — language of the generated prompt


class LLMPromptResponse(BaseModel):
    ok: bool
    prompt: str | None = None
    title: str | None = None
    provider: str
    model: str
    latency_ms: int | None = None
    error: str | None = None


def _build_prompt_system(language: str, orientation_hint: str) -> str:
    if language == "zh":
        prompt_lang_rule = "提示词以中文为主、简洁有力，适当加入镜头术语（如 close-up、wide shot、cinematic lighting、depth of field）。"
        prompt_fmt = "PROMPT: <中文画面提示词，40-80个词>\nTITLE: <中文短视频标题，10字以内>"
    else:
        prompt_lang_rule = "提示词以英文为主、简洁有力，适当加入镜头术语（如 close-up、wide shot、cinematic lighting、depth of field）。"
        prompt_fmt = "PROMPT: <英文画面提示词，40-80个词>\nTITLE: <中文短视频标题，10字以内>"
    return (
        "你是一名资深的分镜师与短视频编导，专门为 AI 视频生图工具编写画面提示词。\n"
        "规则：\n"
        "1. 只描述画面中的动作、构图、镜头、环境、光影与氛围；绝不描述角色的五官或外观细节（角色外观由参考图控制）。\n"
        f"2. {prompt_lang_rule}\n"
        "3. 不要加入任何画风/材质描述词（例如不要写 \"Pixar style\"、\"realistic photo\"、\"anime style\" 等），画风前缀由系统自动处理。\n"
        f"4. 必须考虑{orientation_hint}构图，画面主体突出、有视觉冲击力。\n"
        "5. 输出必须严格按以下格式，只有两行：\n"
        f"{prompt_fmt}"
    )


def _parse_prompt_reply(reply: str) -> tuple[str, str]:
    prompt = ""
    title = ""
    for line in reply.splitlines():
        line = line.strip()
        if line.upper().startswith("PROMPT:"):
            prompt = line[len("PROMPT:"):].strip()
        elif line.upper().startswith("TITLE:"):
            title = line[len("TITLE:"):].strip()
    if not prompt:
        prompt = reply.strip()
    return prompt, title


@router.post("/generate-prompt", response_model=LLMPromptResponse)
async def llm_generate_prompt(body: LLMPromptRequest):
    """Generate a scene image prompt (random or keyword-driven) via the configured LLM."""
    cfg = _resolve_provider_settings(body.provider)
    provider = cfg["provider"]

    language = body.language if body.language in ("en", "zh") else "en"
    orientation_hint = "竖屏 9:16 短视频构图" if body.orientation == "VERTICAL" else "横屏 16:9 电影构图"
    system = _build_prompt_system(language, orientation_hint)
    if body.mode == "keyword" and body.keywords and body.keywords.strip():
        user = f"请根据以下关键词与需求生成一个画面提示词：{body.keywords.strip()}"
    else:
        user = "请随机生成一个通用的画面提示词，题材不限，尽量有故事感与画面张力。"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    start = time.perf_counter()
    try:
        reply = await _run_chat(cfg, messages, 512)
        latency = int((time.perf_counter() - start) * 1000)
        prompt, title = _parse_prompt_reply(reply)
        logger.info("LLM prompt gen ok: provider=%s mode=%s latency=%dms", provider, body.mode, latency)
        return LLMPromptResponse(
            ok=True,
            prompt=prompt,
            title=title,
            provider=provider,
            model=cfg.get("model") or "unknown",
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.warning("LLM prompt gen failed: provider=%s err=%s", provider, e)
        return LLMPromptResponse(
            ok=False,
            provider=provider,
            model=cfg.get("model") or "unknown",
            latency_ms=latency,
            error=f"{type(e).__name__}: {e}",
        )
