"""Generic key-value settings API (JSON file storage).

Sections are namespaced dicts, e.g. {"llm": {...}, "tts": {...}},
so future settings groups can be added without schema changes.
"""
import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agent.config import BASE_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

SETTINGS_FILE = BASE_DIR / "settings.json"
_lock = threading.Lock()

_DEFAULTS: dict = {}


def _load() -> dict:
    with _lock:
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("settings.json unreadable, using defaults: %s", e)
        return dict(_DEFAULTS)


def _save(data: dict):
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("")
async def get_all():
    """Return all settings sections."""
    return _load()


@router.get("/{section}")
async def get_section(section: str):
    data = _load()
    if section not in data:
        raise HTTPException(404, f"Settings section '{section}' not found")
    return data[section]


@router.put("/{section}")
async def upsert_section(section: str, body: dict):
    """Merge body into the given settings section (partial update)."""
    if not section or not section.isidentifier():
        raise HTTPException(400, "Invalid section name")
    data = _load()
    current = data.get(section)
    if not isinstance(current, dict):
        current = {}
    current.update(body)
    data[section] = current
    _save(data)
    logger.info("Settings updated: section=%s keys=%s", section, sorted(body.keys()))
    return data[section]


@router.delete("/{section}/{key}")
async def delete_key(section: str, key: str):
    data = _load()
    if section in data and isinstance(data[section], dict) and key in data[section]:
        del data[section][key]
        _save(data)
        return {"ok": True, "deleted": key}
    raise HTTPException(404, f"Key '{section}.{key}' not found")
