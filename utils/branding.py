"""Shared platform branding helpers for clone and Business Automation welcomes."""

from __future__ import annotations

from handlers.common.clone_context import MAIN_BOT_USERNAME
from database.seller_subscriptions import get_config, effective_plan
import asyncio
import time

_BRANDING_CACHE = None
_BRANDING_CACHE_TS = 0.0
_OWNER_PLAN_CACHE = {}
_OWNER_PLAN_CACHE_TTL = 15.0

async def _branding_config_cached():
    global _BRANDING_CACHE, _BRANDING_CACHE_TS
    now = time.monotonic()
    if _BRANDING_CACHE is not None and (now - _BRANDING_CACHE_TS) < 15.0:
        return _BRANDING_CACHE
    value = await get_config()
    _BRANDING_CACHE = value
    _BRANDING_CACHE_TS = now
    return value

async def _effective_plan_cached(owner_id):
    now = time.monotonic()
    key = int(owner_id)
    cached = _OWNER_PLAN_CACHE.get(key)
    if cached and (now - cached[0]) < _OWNER_PLAN_CACHE_TTL:
        return cached[1]
    value = await effective_plan(key)
    _OWNER_PLAN_CACHE[key] = (now, value)
    return value

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


def default_branding_text() -> str:
    username = str(MAIN_BOT_USERNAME or "").lstrip("@").strip()
    return f"🤖 Powered by @{username}" if username else "🤖 Powered by Main Bot"


async def branding_settings() -> tuple[bool, str]:
    cfg = await _branding_config_cached()
    enabled = bool(cfg.get("branding_enabled", True))
    text = str(cfg.get("branding_text") or "").strip() or default_branding_text()
    return enabled, text


async def append_branding(text: str) -> str:
    base = str(text or "").rstrip()
    enabled, branding = await branding_settings()
    if not enabled or not branding:
        return base
    if branding.casefold() in base.casefold():
        return base
    if not base:
        return branding
    return f"{base}\n\n{SEPARATOR}\n\n{branding}"


async def branding_settings_for_owner(owner_id: int) -> tuple[bool, str]:
    """Return effective branding visibility for a seller's current plan."""
    cfg = await _branding_config_cached()
    global_enabled = bool(cfg.get("branding_enabled", True))
    text = str(cfg.get("branding_text") or "").strip() or default_branding_text()
    try:
        plan, _assignment = await _effective_plan_cached(int(owner_id))
        plan_enabled = bool(plan.get("branding_enabled", True))
    except Exception:
        # Branding lookup must never break a clone welcome message.
        plan_enabled = True
    return global_enabled and plan_enabled, text


async def append_seller_branding(text: str, owner_id: int) -> str:
    """Append platform branding only when the seller's current plan allows it."""
    base = str(text or "").rstrip()
    enabled, branding = await branding_settings_for_owner(int(owner_id))
    if not enabled or not branding:
        return base
    if branding.casefold() in base.casefold():
        return base
    if not base:
        return branding
    return f"{base}\n\n{SEPARATOR}\n\n{branding}"
