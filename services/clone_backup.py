from __future__ import annotations

import gzip
import hashlib
import json
import io
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from database.mongo import get_database
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError, BulkWriteError

CLONE_BACKUP_FORMAT = "telegram-saas-clone-backup"
CLONE_BACKUP_VERSION = 1

# Clone-owned collections use data_owner_id/owner_id as their scope. Keep the
# seller_bots identity separately so a restore can identify the clone.
# Every collection below is scoped to one clone's data_owner_id/owner_id.
# Keep plan groups, payment configuration and feature settings together with
# the core subscriber records so a restore is a complete clone migration.
CLONE_COLLECTIONS = (
    # Core clone data
    "seller_settings",
    "seller_users",
    "seller_payments",
    "seller_subscriptions",
    "seller_plan_group_subscriptions",
    "seller_plans",
    "seller_plan_groups",
    "seller_plan_group_counters",
    "seller_channels",
    "seller_staff",
    "seller_invoices",
    "seller_referrals",
    "seller_business_accounts",
    "seller_business_contacts",

    # Seller/clone payment setup and seller plan metadata
    "payment_gateway_configs",
    "razorpay_qr_pool",
    # Clone feature configuration and data
    "seller_forced_join",
    "seller_forced_join_pending",
    "seller_forced_join_settings",
    "seller_forced_join_chat_messages",
    "seller_group_manager",
    "seller_content_protection_settings",
    "seller_deleting_message_settings",
    "seller_deleting_message_stats",
    "seller_broadcast_drafts",
    "seller_scheduled_broadcasts",
    "seller_coupons",
    "seller_failed_deliveries",
    "seller_referral_commissions",
    "seller_ai_assistant",
    "seller_ai_events",
    "seller_ai_unanswered",
    "seller_ai_sessions",
    "seller_official_business_connections",
    # Business Automation editors store their payloads in dedicated collections,
    # not only in seller_settings. These were missing from earlier backups.
    "business_automation_welcome",
    "business_automation_auto_reply",
    "business_automation_reply_templates",
    "business_automation_broadcast",
    "business_automation_contact_routes",
    "business_automation_business_recipients",
    "business_payment_delivery_logs",
    "clone_live_support_settings",
    "clone_live_support_topics",
    "clone_live_support_message_links",
    "clone_live_support_blocks",
    "clone_live_support_templates",
    "clone_live_support_auto_replies",
    "clone_live_support_deliveries",
    "clone_referral_unlocks",
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


def _backup_scope_query(name: str, scope: int, bot_id: int) -> dict[str, Any]:
    """Return the exact source-clone scope for each collection.

    Most clone collections are keyed by owner_id=data_owner_id. A few use a
    scope discriminator, or a counter document whose _id is the data scope.
    Keep these exceptions explicit so one seller's other clone data is never
    swept into this backup.
    """
    if name == "seller_plan_group_counters":
        return {"$or": [{"owner_id": scope}, {"_id": scope}]}
    if name == "payment_gateway_configs":
        return {"owner_id": scope, "scope": "seller"}
    if name == "razorpay_qr_pool":
        return {"owner_id": scope, "bot_id": int(bot_id)}
    return {"owner_id": scope}


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
        query = _backup_scope_query(name, scope, int(bot_id))
        docs = await db[name].find(query).to_list(length=None)
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
            # The QR file_id is stored on seller_bots, not seller_settings.
            # Copy only this clone's payment asset; never copy its token/identity.
            "payment_qr_file_id": str(bot_record.get("payment_qr_file_id") or ""),
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


MAX_CLONE_BACKUP_BYTES = 20 * 1024 * 1024
MAX_CLONE_BACKUP_DECOMPRESSED_BYTES = 100 * 1024 * 1024
RESTORE_BATCH_SIZE = 250


def _safe_gzip_decompress(raw: bytes) -> bytes:
    """Decompress a clone backup without allowing unbounded gzip expansion."""
    if len(raw) > MAX_CLONE_BACKUP_BYTES:
        raise ValueError("Backup file exceeds the 20 MB safety limit")
    if not raw.startswith(b"\x1f\x8b"):
        raise ValueError("Invalid or damaged clone backup file")
    output = io.BytesIO()
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_CLONE_BACKUP_DECOMPRESSED_BYTES:
                    raise ValueError("Decompressed backup exceeds the 100 MB safety limit")
                output.write(chunk)
    except (OSError, EOFError) as exc:
        raise ValueError("Invalid or damaged clone backup file") from exc
    return output.getvalue()


def _load_clone_backup(raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = json.loads(_safe_gzip_decompress(raw).decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid or damaged clone backup file") from exc
    if not isinstance(envelope, dict):
        raise ValueError("Invalid clone backup structure")
    manifest = envelope.get("manifest") or {}
    payload = envelope.get("payload") or {}
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        raise ValueError("Invalid clone backup manifest or payload")
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
    actual_records = sum(len(collections.get(name, [])) for name in CLONE_COLLECTIONS)
    if int(manifest.get("records", -1)) != actual_records:
        raise ValueError("Backup record count does not match manifest")
    if actual_records > 100_000:
        raise ValueError("Backup exceeds the 100,000 record safety limit")
    return manifest, payload


def validate_clone_backup(raw: bytes) -> dict[str, Any]:
    manifest, _payload = _load_clone_backup(raw)
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
    """Stable per-clone keys used for idempotent Merge operations."""
    fields = {
        "seller_settings": ("owner_id",),
        "seller_users": ("owner_id", "user_id"),
        "seller_payments": ("owner_id", "payment_id"),
        "seller_subscriptions": ("owner_id", "user_id"),
        "seller_plan_group_subscriptions": ("owner_id", "user_id", "group_id"),
        "seller_plans": ("owner_id", "plan_id"),
        "seller_plan_groups": ("owner_id", "group_id"),
        "seller_plan_group_counters": ("owner_id",),
        "seller_channels": ("owner_id", "chat_id"),
        "seller_staff": ("owner_id", "user_id"),
        "seller_invoices": ("owner_id", "invoice_id"),
        "seller_referrals": ("owner_id", "referred_user_id"),
        "seller_business_accounts": ("owner_id", "account_user_id"),
        "seller_business_contacts": ("owner_id", "account_user_id", "peer_user_id"),
        "payment_gateway_configs": ("scope", "owner_id"),
        "seller_subscription_settings": ("owner_id",),
        "seller_plan_assignments": ("owner_id",),
        "seller_forced_join_settings": ("owner_id",),
        "seller_content_protection_settings": ("owner_id",),
        "seller_deleting_message_settings": ("owner_id",),
        "seller_ai_assistant": ("owner_id",),
        "clone_live_support_settings": ("owner_id",),
        "seller_official_business_connections": ("owner_id", "account_user_id"),
        "clone_live_support_topics": ("owner_id", "user_id"),
        "clone_live_support_blocks": ("owner_id", "user_id"),
        "clone_live_support_templates": ("owner_id", "command"),
        "clone_live_support_auto_replies": ("owner_id", "keyword"),
        "clone_referral_unlocks": ("owner_id", "user_id"),
        "seller_forced_join": ("owner_id", "access_chat_id", "chat_id"),
        "seller_forced_join_pending": ("owner_id", "user_id", "access_chat_id"),
        "seller_forced_join_chat_messages": ("owner_id", "access_chat_id"),
        "business_automation_contact_routes": ("owner_id", "user_id", "mode"),
        "business_automation_business_recipients": ("owner_id", "business_user_id"),
        "business_automation_welcome": ("owner_id",),
        "business_automation_auto_reply": ("owner_id", "reply_id"),
        "business_automation_reply_templates": ("owner_id", "template_id"),
        "business_automation_broadcast": ("owner_id",),
        "business_payment_delivery_logs": ("owner_id", "delivery_id"),
        "razorpay_qr_pool": ("owner_id", "bot_id", "qr_code_id"),
    }.get(collection)

    if collection == "seller_plan_group_counters":
        if document.get("_id") is not None:
            return {"_id": document["_id"]}
        if document.get("owner_id") is not None:
            return {"_id": int(document["owner_id"])}
        return {}
    if fields:
        key = {field: document[field] for field in fields
               if field in document and document[field] is not None}
        return key if len(key) == len(fields) else {}

    # Event/history collections generally have no declared unique natural key.
    # Keep them as append-only records during Merge rather than accidentally
    # overwriting unrelated entries.
    return {}



async def restore_clone_backup(
    raw: bytes,
    target_owner_id: int,
    mode: str,
    progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Restore a verified clone backup using bounded MongoDB bulk batches."""
    if mode not in {"merge", "replace"}:
        raise ValueError("Choose Merge or Replace")
    _manifest, payload = _load_clone_backup(raw)
    source_collections = payload["collections"]

    docs_by_collection: dict[str, list[dict[str, Any]]] = {}
    total = sum(len(source_collections.get(name, [])) for name in CLONE_COLLECTIONS)
    for name in CLONE_COLLECTIONS:
        rows = source_collections.get(name, [])
        if not isinstance(rows, list):
            raise ValueError(f"Invalid records for {name}")
        decoded: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"Invalid record in {name}")
            doc = _decode_backup_value(row)
            # The plan-group counter is deliberately keyed by _id=data_owner_id.
            # Preserve/rebind that key; dropping it would silently break future
            # PLAN_ID allocation after a Replace restore.
            if name == "seller_plan_group_counters":
                doc["_id"] = int(target_owner_id)
                if "owner_id" in doc:
                    doc["owner_id"] = int(target_owner_id)
            else:
                doc.pop("_id", None)
            if "owner_id" in doc:
                doc["owner_id"] = int(target_owner_id)
            if "data_owner_id" in doc:
                doc["data_owner_id"] = int(target_owner_id)
            if name == "razorpay_qr_pool":
                target_meta = await get_database()["seller_bots"].find_one({"data_owner_id": int(target_owner_id)}, {"bot_id": 1})
                if target_meta and target_meta.get("bot_id") is not None:
                    doc["bot_id"] = int(target_meta["bot_id"])
            decoded.append(doc)
        docs_by_collection[name] = decoded

    db = get_database()
    restored = 0
    skipped = 0
    done = 0
    if mode == "replace":
        # Delete only target-clone records, preserving the source clone and seller records.
        for name in CLONE_COLLECTIONS:
            if name == "seller_plan_group_counters":
                await db[name].delete_many({"$or": [{"owner_id": int(target_owner_id)}, {"_id": int(target_owner_id)}]})
            elif name == "payment_gateway_configs":
                await db[name].delete_many({"owner_id": int(target_owner_id), "scope": "seller"})
            else:
                await db[name].delete_many({"owner_id": int(target_owner_id)})

    for name in CLONE_COLLECTIONS:
        docs = docs_by_collection[name]
        for offset in range(0, len(docs), RESTORE_BATCH_SIZE):
            batch = docs[offset:offset + RESTORE_BATCH_SIZE]
            if mode == "replace":
                if batch:
                    result = await db[name].insert_many(batch, ordered=True)
                    restored += len(result.inserted_ids)
            else:
                operations = []
                direct_inserts = []
                for doc in batch:
                    key = _natural_key(name, doc)
                    if key:
                        operations.append(UpdateOne(key, {"$setOnInsert": doc}, upsert=True))
                    else:
                        direct_inserts.append(doc)
                if operations:
                    try:
                        result = await db[name].bulk_write(operations, ordered=False)
                        inserted = int(result.upserted_count or 0)
                        restored += inserted
                        skipped += len(operations) - inserted
                    except BulkWriteError as exc:
                        details = exc.details or {}
                        errors = details.get("writeErrors", [])
                        if any(int(item.get("code", 0)) != 11000 for item in errors):
                            raise
                        inserted = int(details.get("nUpserted", 0) or 0)
                        restored += inserted
                        skipped += max(0, len(operations) - inserted)
                if direct_inserts:
                    try:
                        result = await db[name].insert_many(direct_inserts, ordered=False)
                        restored += len(result.inserted_ids)
                    except BulkWriteError as exc:
                        details = exc.details or {}
                        errors = details.get("writeErrors", [])
                        if any(int(item.get("code", 0)) != 11000 for item in errors):
                            raise
                        inserted = int(details.get("nInserted", 0) or 0)
                        restored += inserted
                        skipped += max(0, len(direct_inserts) - inserted)
            done += len(batch)
            if progress_callback:
                await progress_callback(done, max(total, 1), name)
    # Restore the per-clone QR asset onto the TARGET clone record. Bot token,
    # bot_id, username and seller ownership are intentionally never overwritten.
    clone_meta = payload.get("clone") or {}
    target_bot = await db["seller_bots"].find_one({"data_owner_id": int(target_owner_id)})
    if target_bot and "payment_qr_file_id" in clone_meta:
        await db["seller_bots"].update_one(
            {"_id": target_bot["_id"]},
            {"$set": {
                "payment_qr_file_id": str(clone_meta.get("payment_qr_file_id") or ""),
                "updated_at": datetime.now(timezone.utc),
            }},
        )

    if progress_callback and total == 0:
        await progress_callback(0, 0, "Backup contains no records")
    return {"restored": restored, "skipped": skipped, "records": total}

