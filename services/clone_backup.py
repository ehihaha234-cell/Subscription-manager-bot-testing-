from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from database.mongo import get_database
from pymongo.errors import DuplicateKeyError

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


def validate_clone_backup(raw: bytes) -> dict[str, Any]:
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("Backup file exceeds the 20 MB safety limit")
    try:
        envelope = json.loads(gzip.decompress(raw).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid or damaged clone backup file") from exc
    manifest = envelope.get("manifest") or {}
    payload = envelope.get("payload") or {}
    if payload.get("format") != CLONE_BACKUP_FORMAT or manifest.get("format") != CLONE_BACKUP_FORMAT:
        raise ValueError("This is not a supported Clone Bot backup")
    if payload.get("version") != CLONE_BACKUP_VERSION or manifest.get("version") != CLONE_BACKUP_VERSION:
        raise ValueError("Unsupported Clone Bot backup version")
    canonical = json.dumps(payload, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != manifest.get("sha256"):
        raise ValueError("Backup checksum verification failed")
    collections = payload.get("collections")
    if not isinstance(collections, dict) or set(collections) - set(CLONE_COLLECTIONS):
        raise ValueError("Backup contains missing or unsupported collections")
    if any(not isinstance(collections.get(name, []), list) for name in CLONE_COLLECTIONS):
        raise ValueError("Backup contains invalid collection records")
    return manifest


def _decode_backup_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_backup_value(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$date"}:
            raw = str(value["$date"])
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        if set(value) == {"$oid"}:
            from bson import ObjectId
            return ObjectId(str(value["$oid"]))
        return {key: _decode_backup_value(item) for key, item in value.items()}
    return value


def _natural_key(collection: str, document: dict[str, Any]) -> dict[str, Any]:
    """Choose stable fields for merge deduplication without matching _id across clones."""
    candidates = {
        "seller_settings": ("owner_id",),
        "seller_users": ("owner_id", "user_id"),
        "seller_payments": ("owner_id", "payment_id"),
        "seller_subscriptions": ("owner_id", "user_id"),
        "seller_plan_group_subscriptions": ("owner_id", "user_id", "group_id"),
        "seller_plans": ("owner_id", "plan_id"),
        "seller_channels": ("owner_id", "chat_id"),
        "seller_staff": ("owner_id", "user_id"),
        "seller_invoices": ("owner_id", "invoice_id"),
    }.get(collection, ("owner_id", "_id"))
    key = {field: document[field] for field in candidates if field in document and document[field] is not None}
    if collection == "seller_settings":
        return {"owner_id": document.get("owner_id")}
    return key if len(key) == len(candidates) else {}


async def restore_clone_backup(
    raw: bytes,
    target_owner_id: int,
    mode: str,
    progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Restore a verified clone backup into this clone's data scope only."""
    if mode not in {"merge", "replace"}:
        raise ValueError("Choose Merge or Replace")
    validate_clone_backup(raw)
    try:
        envelope = json.loads(gzip.decompress(raw).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid or damaged clone backup file") from exc
    manifest = envelope.get("manifest") or {}
    payload = envelope.get("payload") or {}
    if payload.get("format") != CLONE_BACKUP_FORMAT or manifest.get("format") != CLONE_BACKUP_FORMAT:
        raise ValueError("This is not a supported Clone Bot backup")
    if payload.get("version") != CLONE_BACKUP_VERSION or manifest.get("version") != CLONE_BACKUP_VERSION:
        raise ValueError("Unsupported Clone Bot backup version")
    canonical = json.dumps(payload, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("Backup checksum verification failed")
    source_collections = payload.get("collections")
    if not isinstance(source_collections, dict):
        raise ValueError("Backup collections are missing")
    unknown = set(source_collections) - set(CLONE_COLLECTIONS)
    if unknown:
        raise ValueError("Backup contains unsupported collections")
    docs_by_collection = {}
    total = sum(len(v) for v in source_collections.values() if isinstance(v, list))
    done = 0
    for name in CLONE_COLLECTIONS:
        rows = source_collections.get(name, [])
        if not isinstance(rows, list):
            raise ValueError(f"Invalid records for {name}")
        decoded = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"Invalid record in {name}")
            doc = _decode_backup_value(row)
            doc.pop("_id", None)
            if "owner_id" in doc:
                doc["owner_id"] = int(target_owner_id)
            if "data_owner_id" in doc:
                doc["data_owner_id"] = int(target_owner_id)
            decoded.append(doc)
        docs_by_collection[name] = decoded

    db = get_database()
    restored = 0
    skipped = 0
    # Replace deletes only the target clone's scoped rows, never source clone or seller records.
    if mode == "replace":
        for name in CLONE_COLLECTIONS:
            await db[name].delete_many({"owner_id": int(target_owner_id)})
    for name in CLONE_COLLECTIONS:
        for doc in docs_by_collection[name]:
            if mode == "merge":
                key = _natural_key(name, doc)
                if key:
                    existing = await db[name].find_one(key, {"_id": 1})
                    if existing:
                        skipped += 1
                        done += 1
                        if progress_callback:
                            await progress_callback(done, max(total, 1), name)
                        continue
            try:
                await db[name].insert_one(doc)
                restored += 1
            except DuplicateKeyError:
                # Existing target data can collide with a MongoDB unique index
                # even when the natural-key lookup above did not find it.
                # During Merge, preserve the target record and continue safely.
                if mode != "merge":
                    raise
                skipped += 1
            done += 1
            if progress_callback:
                await progress_callback(done, max(total, 1), name)
    return {"restored": restored, "skipped": skipped, "records": total}
