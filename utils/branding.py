"""Shared platform branding helpers for clone and Business Automation welcomes."""

from __future__ import annotations

from datetime import datetime, timezone

from handlers.common.clone_context import MAIN_BOT_USERNAME
import asyncio
from database.seller_subscriptions import get_config, get_assignment

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


async def branding_settings_for_owner(owner_id: int) -> tuple[bool, str]:
    """Return effective branding visibility with independent reads in parallel."""
    try:
        cfg, assignment = await asyncio.gather(
            get_config(),
            get_assignment(int(owner_id)),
        )
        global_enabled = bool(cfg.get("branding_enabled", True))
        text = str(cfg.get("branding_text") or "").strip() or default_branding_text()

        plan_enabled = True
        if assignment:
            expiry = assignment.get("expiry_date")
            now = datetime.now(timezone.utc)
            if expiry and getattr(expiry, "tzinfo", None) is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry and expiry <= now:
                plan_id = "free"
            else:
                plan_id = assignment.get("plan_id", "free")
            if plan_id != "free":
                paid = next(
                    (p for p in cfg.get("paid_plans", []) if p.get("plan_id") == plan_id),
                    None,
                )
                if paid and paid.get("active", True):
                    plan_enabled = bool(paid.get("branding_enabled", True))
        return global_enabled and plan_enabled, text
    except Exception:
        # Branding must never delay/fail the welcome.
        return True, default_branding_text()


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
