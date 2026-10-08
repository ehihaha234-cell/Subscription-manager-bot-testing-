from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from database.mongo import get_database

CLONE_BACKUP_FORMAT = "telegram-saas-clone-backup"
CLONE_BACKUP_VERSION = 1

# Clone-owned collections use data_owner_id/owner_id as their scope. Keep the
# seller_bots identity separately so a restore can identify the clone.
CLONE_COLLECTIONS = (
    "seller_settings",
    "seller_users",
    "seller_payments",
    "seller_subscriptions",
    "seller_plan_group_subscriptions",
    "seller_plans",
    "seller_channels",
    "seller_staff",
    "seller_invoices",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {"$date": value.astimezone(timezone.utc).isoformat()}
    try:
        from bson import ObjectId
        if isinstance(value, ObjectId):
            return {"$oid": str(value)}
    except Exception:
        pass
    raise TypeError(f"Unsupported backup value: {type(value).__name__}")


async def create_clone_backup(
    owner_id: int,
    bot_id: int,
    bot_username: str = "",
    progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
):
    db = get_database()
    scope = int(owner_id)
    collections: dict[str, list[dict[str, Any]]] = {}
    records = []
    for name in CLONE_COLLECTIONS:
        # All clone-owned collections in the current code use owner_id as the
        # data scope. Do not copy another clone's documents.
        docs = await db[name].find({"owner_id": scope}).to_list(length=None)
        collections[name] = docs
        records.extend((name, d) for d in docs)

    total = len(records) + 1
    done = 0
    if progress_callback:
        await progress_callback(done, total, "Preparing clone backup")
    for name, _doc in records:
        done += 1
        if progress_callback:
            await progress_callback(done, total, name)

    bot_record = await db["seller_bots"].find_one({"bot_id": int(bot_id)}) or {}
    payload = {
        "format": CLONE_BACKUP_FORMAT,
        "version": CLONE_BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc),
        "clone": {
            "bot_id": int(bot_id),
            "bot_username": str(bot_username or ""),
            "owner_id": scope,
            "data_owner_id": scope,
            "seller_account_id": int(bot_record.get("owner_id") or 0),
        },
        "collections": collections,
    }
    canonical = json.dumps(payload, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "format": CLONE_BACKUP_FORMAT,
        "version": CLONE_BACKUP_VERSION,
        "records": sum(len(v) for v in collections.values()),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    envelope = {"manifest": manifest, "payload": payload}
    raw = gzip.compress(json.dumps(envelope, default=_json_default, ensure_ascii=False).encode(), compresslevel=9)
    if progress_callback:
        await progress_callback(total, total, "Backup complete")
    return raw, manifest
