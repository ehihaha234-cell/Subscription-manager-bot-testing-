"""Shared platform branding helpers for clone and Business Automation welcomes."""

from __future__ import annotations

from handlers.common.clone_context import MAIN_BOT_USERNAME
from database.seller_subscriptions import get_config

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


def default_branding_text() -> str:
    username = str(MAIN_BOT_USERNAME or "").lstrip("@").strip()
    return f"🤖 Powered by @{username}" if username else "🤖 Powered by Main Bot"


async def branding_settings() -> tuple[bool, str]:
    cfg = await get_config()
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
