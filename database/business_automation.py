"""Separate MongoDB storage for Business Automation message editors.

Welcome message, auto reply, and reply templates intentionally use separate
collections. Seller-wide runtime switches remain in seller settings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from database.mongo import get_database

WELCOME_COLLECTION = "business_automation_welcome"
AUTO_REPLY_COLLECTION = "business_automation_auto_reply"
TEMPLATE_COLLECTION = "business_automation_reply_templates"


def _now():
    return datetime.now(timezone.utc)


def _collection(name: str):
    """Return the initialized MongoDB collection.

    ``get_database`` is synchronous; awaiting it caused every Business
    Automation editor callback to fail before rendering.
    """
    return get_database()[name]


async def get_business_welcome(owner_id: int) -> dict:
    col = _collection(WELCOME_COLLECTION)
    doc = await col.find_one({"owner_id": int(owner_id)}, {"_id": 0})
    if doc:
        return doc
    # One-time transparent migration from the previous seller-settings fields.
    from database.seller_data import get_seller_settings
    legacy = await get_seller_settings(owner_id)
    item = {
        "owner_id": int(owner_id),
        "enabled": bool(legacy.get("business_welcome_enabled", True)),
        "text": str(legacy.get("business_welcome_message") or ""),
        "media_type": str(legacy.get("business_welcome_media_type") or ""),
        "media_file_id": str(legacy.get("business_welcome_media_file_id") or ""),
        "media": ([{"type": str(legacy.get("business_welcome_media_type") or "document"), "file_id": str(legacy.get("business_welcome_media_file_id") or "")}] if legacy.get("business_welcome_media_file_id") else []),
        "buttons": legacy.get("business_welcome_buttons") or [],
        "buttons_input": str(legacy.get("business_welcome_buttons_input") or ""),
    }
    if item["text"] or item["media_file_id"] or item["buttons"]:
        return await update_business_welcome(owner_id, **{k: v for k, v in item.items() if k != "owner_id"})
    return item


async def update_business_welcome(owner_id: int, **fields) -> dict:
    allowed = {"enabled", "text", "media_type", "media_file_id", "media", "buttons", "buttons_input"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    payload["updated_at"] = _now()
    col = _collection(WELCOME_COLLECTION)
    await col.update_one(
        {"owner_id": int(owner_id)},
        {"$set": payload, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    return await get_business_welcome(owner_id)


async def get_business_auto_reply(owner_id: int) -> dict:
    col = _collection(AUTO_REPLY_COLLECTION)
    doc = await col.find_one({"owner_id": int(owner_id)}, {"_id": 0})
    if doc:
        return doc
    from database.seller_data import get_seller_settings
    legacy = await get_seller_settings(owner_id)
    item = {
        "owner_id": int(owner_id),
        "enabled": bool(legacy.get("business_auto_reply_enabled", True)),
        "text": str(legacy.get("business_auto_reply_message") or ""),
        "media_type": str(legacy.get("business_auto_reply_media_type") or ""),
        "media_file_id": str(legacy.get("business_auto_reply_media_file_id") or ""),
        "media": ([{"type": str(legacy.get("business_auto_reply_media_type") or "document"), "file_id": str(legacy.get("business_auto_reply_media_file_id") or "")}] if legacy.get("business_auto_reply_media_file_id") else []),
        "buttons": legacy.get("business_auto_reply_buttons") or [],
        "buttons_input": str(legacy.get("business_auto_reply_buttons_input") or ""),
    }
    if item["text"] or item["media_file_id"] or item["buttons"]:
        return await update_business_auto_reply(owner_id, **{k: v for k, v in item.items() if k != "owner_id"})
    return item


async def update_business_auto_reply(owner_id: int, **fields) -> dict:
    allowed = {"enabled", "text", "media_type", "media_file_id", "media", "buttons", "buttons_input"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    payload["updated_at"] = _now()
    col = _collection(AUTO_REPLY_COLLECTION)
    await col.update_one(
        {"owner_id": int(owner_id)},
        {"$set": payload, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    return await get_business_auto_reply(owner_id)


async def list_business_reply_templates(owner_id: int) -> list[dict]:
    col = _collection(TEMPLATE_COLLECTION)
    cursor = col.find({"owner_id": int(owner_id)}, {"_id": 0}).sort("created_at", 1)
    docs = [doc async for doc in cursor]
    if docs:
        return docs
    from database.seller_data import get_seller_settings
    legacy = await get_seller_settings(owner_id)
    for old in legacy.get("business_reply_templates") or []:
        if not isinstance(old, dict):
            continue
        created = await create_business_reply_template(
            owner_id,
            str(old.get("shortcut") or "template"),
            str(old.get("name") or old.get("shortcut") or "Template"),
        )
        await update_business_reply_template(
            owner_id,
            created["template_id"],
            text=str(old.get("text") or ""),
            media_type=str(old.get("media_type") or ""),
            media_file_id=str(old.get("media_file_id") or ""),
            media=old.get("media") or [],
            buttons=old.get("buttons") or [],
            buttons_input=str(old.get("buttons_input") or ""),
        )
    if legacy.get("business_reply_templates"):
        cursor = col.find({"owner_id": int(owner_id)}, {"_id": 0}).sort("created_at", 1)
        return [doc async for doc in cursor]
    return []


async def get_business_reply_template(owner_id: int, template_id: str) -> dict | None:
    col = _collection(TEMPLATE_COLLECTION)
    return await col.find_one(
        {"owner_id": int(owner_id), "template_id": str(template_id)},
        {"_id": 0},
    )


async def create_business_reply_template(owner_id: int, shortcut: str, name: str) -> dict:
    doc = {
        "owner_id": int(owner_id),
        "template_id": uuid4().hex[:12],
        "shortcut": shortcut[:64],
        "name": name[:80],
        "text": "",
        "media_type": "",
        "media_file_id": "",
        "media": [],
        "buttons": [],
        "buttons_input": "",
        "enabled": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    col = _collection(TEMPLATE_COLLECTION)
    await col.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def update_business_reply_template(owner_id: int, template_id: str, **fields) -> dict | None:
    allowed = {"shortcut", "name", "text", "media_type", "media_file_id", "media", "buttons", "buttons_input", "enabled"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    payload["updated_at"] = _now()
    col = _collection(TEMPLATE_COLLECTION)
    await col.update_one(
        {"owner_id": int(owner_id), "template_id": str(template_id)},
        {"$set": payload},
    )
    return await get_business_reply_template(owner_id, template_id)


async def delete_business_reply_template(owner_id: int, template_id: str) -> bool:
    col = _collection(TEMPLATE_COLLECTION)
    result = await col.delete_one({"owner_id": int(owner_id), "template_id": str(template_id)})
    return bool(result.deleted_count)

# Keyword-based auto replies. Each keyword has its own common-editor payload.
async def list_business_auto_replies(owner_id: int) -> list[dict]:
    col = _collection(AUTO_REPLY_COLLECTION)
    cursor = col.find(
        {"owner_id": int(owner_id), "reply_id": {"$exists": True}},
        {"_id": 0},
    ).sort("created_at", 1)
    return [doc async for doc in cursor]


async def get_business_auto_reply_item(owner_id: int, reply_id: str) -> dict | None:
    return await _collection(AUTO_REPLY_COLLECTION).find_one(
        {"owner_id": int(owner_id), "reply_id": str(reply_id)}, {"_id": 0}
    )


async def create_business_auto_reply_item(owner_id: int, keyword: str) -> dict:
    keyword = " ".join(str(keyword or "").strip().lower().split())
    if not keyword:
        raise ValueError("Keyword is required")
    col = _collection(AUTO_REPLY_COLLECTION)
    existing = await col.find_one({"owner_id": int(owner_id), "keyword": keyword, "reply_id": {"$exists": True}})
    if existing:
        raise ValueError("This keyword already exists")
    doc = {
        "owner_id": int(owner_id),
        "reply_id": uuid4().hex[:12],
        "keyword": keyword[:100],
        "enabled": True,
        "text": "",
        "media_type": "",
        "media_file_id": "",
        "media": [],
        "buttons": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    await col.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def update_business_auto_reply_item(owner_id: int, reply_id: str, **fields) -> dict | None:
    allowed = {"keyword", "enabled", "text", "media_type", "media_file_id", "media", "buttons"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if "keyword" in payload:
        payload["keyword"] = " ".join(str(payload["keyword"]).strip().lower().split())[:100]
    payload["updated_at"] = _now()
    await _collection(AUTO_REPLY_COLLECTION).update_one(
        {"owner_id": int(owner_id), "reply_id": str(reply_id)}, {"$set": payload}
    )
    return await get_business_auto_reply_item(owner_id, reply_id)


async def delete_business_auto_reply_item(owner_id: int, reply_id: str) -> bool:
    result = await _collection(AUTO_REPLY_COLLECTION).delete_one(
        {"owner_id": int(owner_id), "reply_id": str(reply_id)}
    )
    return bool(result.deleted_count)

BROADCAST_COLLECTION = "business_automation_broadcast"
BUSINESS_RECIPIENT_COLLECTION = "business_automation_business_recipients"

async def get_business_broadcast(owner_id: int) -> dict:
    doc = await _collection(BROADCAST_COLLECTION).find_one({"owner_id": int(owner_id)}, {"_id": 0})
    return doc or {"owner_id": int(owner_id), "text": "", "media_type": "", "media_file_id": "", "media": [], "buttons": []}

async def update_business_broadcast(owner_id: int, **fields) -> dict:
    allowed = {"text", "media_type", "media_file_id", "media", "buttons", "last_report", "last_sent_at"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    payload["updated_at"] = _now()
    update = {"$set": payload, "$setOnInsert": {"created_at": _now()}}

    # Keep cumulative broadcast statistics. Older records without these fields
    # remain compatible and start counting from the first broadcast after this patch.
    report = payload.get("last_report")
    if isinstance(report, dict):
        full = int(report.get("full", report.get("fully_delivered", report.get("sent", 0))) or 0)
        partial = int(report.get("partial", report.get("partially_delivered", 0)) or 0)
        failed = int(report.get("failed", 0) or 0)
        total = int(report.get("total", full + partial + failed) or 0)
        update["$inc"] = {
            "broadcasts_sent": 1,
            "broadcast_recipients": total,
            "broadcast_fully_delivered": full,
            "broadcast_partially_delivered": partial,
            "broadcast_failed": failed,
        }

    await _collection(BROADCAST_COLLECTION).update_one(
        {"owner_id": int(owner_id)}, update, upsert=True
    )
    return await get_business_broadcast(owner_id)

async def upsert_business_recipient(owner_id: int, connection_id: str, chat_id: int, user=None) -> None:
    await _collection(BUSINESS_RECIPIENT_COLLECTION).update_one(
        {"owner_id": int(owner_id), "connection_id": str(connection_id), "chat_id": int(chat_id)},
        {"$set": {
            "user_id": int(getattr(user, "id", 0) or chat_id),
            "first_name": str(getattr(user, "first_name", "") or ""),
            "last_name": str(getattr(user, "last_name", "") or ""),
            "username": str(getattr(user, "username", "") or ""),
            "active": True,
            "last_seen_at": _now(),
            "updated_at": _now(),
        }, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def mark_business_recipient_inactive(owner_id: int, connection_id: str, chat_id: int, reason: str = "") -> None:
    await _collection(BUSINESS_RECIPIENT_COLLECTION).update_one(
        {"owner_id": int(owner_id), "connection_id": str(connection_id), "chat_id": int(chat_id)},
        {"$set": {"active": False, "inactive_reason": str(reason or "")[:300], "updated_at": _now()}},
    )

async def list_business_recipients(owner_id: int) -> list[dict]:
    return await _collection(BUSINESS_RECIPIENT_COLLECTION).find(
        {"owner_id": int(owner_id), "active": True}, {"_id": 0}
    ).to_list(length=None)
