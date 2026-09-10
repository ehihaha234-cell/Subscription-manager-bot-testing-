from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any

from database.mongo import get_database

FORMAT = "telegram-saas-clone-backup"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
MAX_DECOMPRESSED = 100 * 1024 * 1024
MAX_RECORDS = 100_000
EXCLUDED = {"seller_bots", "backup_restore_locks"}

# Collections used by clone data. We discover all collections containing the
# clone's owner_id/data_owner_id so new seller features are included too.

def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {"$date": value.astimezone(timezone.utc).isoformat()}
    try:
        from bson import ObjectId
        if isinstance(value, ObjectId):
            return {"$oid": str(value)}
    except ImportError:
        pass
    raise TypeError(f"Unsupported backup value: {type(value).__name__}")


def _object_hook(value: dict[str, Any]) -> Any:
    if set(value) == {"$date"}:
        try:
            return datetime.fromisoformat(str(value["$date"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return value
    if set(value) == {"$oid"}:
        try:
            from bson import ObjectId
            return ObjectId(value["$oid"])
        except Exception:
            return value
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, default=_json_default, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _transform(doc: dict[str, Any], source_scope: int, target_scope: int) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    if out.get("owner_id") == source_scope:
        out["owner_id"] = target_scope
    if out.get("data_owner_id") == source_scope:
        out["data_owner_id"] = target_scope
    # Clone-specific records occasionally retain a nested scope marker.
    if out.get("data_scope_id") == source_scope:
        out["data_scope_id"] = target_scope
    return out


def _identity_query(collection: str, doc: dict[str, Any]) -> dict[str, Any] | None:
    fields = {
        "seller_settings": ("owner_id",),
        "seller_plans": ("owner_id", "plan_id"),
        "seller_channels": ("owner_id", "chat_id"),
        "seller_users": ("owner_id", "user_id"),
        "seller_payments": ("owner_id", "payment_id"),
        "seller_subscriptions": ("owner_id", "user_id"),
        "seller_invoices": ("owner_id", "invoice_id"),
        "seller_referrals": ("owner_id", "referred_user_id"),
        "seller_business_accounts": ("owner_id", "account_user_id"),
        "seller_business_contacts": ("owner_id", "account_user_id", "peer_user_id"),
        "seller_staff": ("owner_id", "user_id"),
        "seller_coupons": ("owner_id", "coupon_code"),
        "seller_content_protection_settings": ("owner_id",),
        "seller_deleting_message_settings": ("owner_id",),
        "seller_forced_join_settings": ("owner_id",),
    }
    chosen = fields.get(collection)
    if chosen and all(doc.get(k) is not None for k in chosen):
        return {k: doc[k] for k in chosen}
    if "owner_id" in doc:
        # For auxiliary collections without a documented unique key, exact
        # owner-scoped document matching is used by the merge path.
        return {"owner_id": doc["owner_id"], **({"_backup_fingerprint": _fingerprint(doc)})}
    return None


def _fingerprint(doc: dict[str, Any]) -> str:
    clean = {k: v for k, v in doc.items() if k not in {"_id", "created_at", "updated_at"}}
    return hashlib.sha256(_canonical(clean)).hexdigest()


async def create_clone_backup(*, owner_id: int, bot_id: int, bot_username: str = "") -> tuple[bytes, dict[str, Any]]:
    db = get_database()
    scope = int(owner_id)
    collections: dict[str, list[dict[str, Any]]] = {}
    total = 0
    names = sorted(n for n in await db.list_collection_names() if n not in EXCLUDED and not n.startswith("system."))
    for name in names:
        docs = []
        cursor = db[name].find({"$or": [{"owner_id": scope}, {"data_owner_id": scope}]})
        async for doc in cursor:
            docs.append(doc)
            total += 1
            if total > MAX_RECORDS:
                raise ValueError(f"Backup exceeds {MAX_RECORDS:,} records")
        if docs:
            collections[name] = docs
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": datetime.now(timezone.utc),
        "source": {"bot_id": int(bot_id), "bot_username": str(bot_username or ""), "scope_id": scope},
        "collections": collections,
    }
    canonical = _canonical(payload)
    manifest = {"format": FORMAT, "version": VERSION, "created_at": payload["created_at"],
                "records": total, "sha256": hashlib.sha256(canonical).hexdigest()}
    raw = _canonical({"manifest": manifest, "payload": payload})
    compressed = gzip.compress(raw, compresslevel=9)
    if len(compressed) > MAX_BYTES:
        raise ValueError("Compressed backup is larger than the 20 MB safety limit")
    return compressed, manifest


def parse_clone_backup(raw: bytes) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    if len(raw) > MAX_BYTES:
        raise ValueError("Backup file exceeds the 20 MB safety limit")
    try:
        decoded = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    except (OSError, EOFError) as exc:
        raise ValueError("Invalid backup file") from exc
    if len(decoded) > MAX_DECOMPRESSED:
        raise ValueError("Backup exceeds the decompressed safety limit")
    try:
        envelope = json.loads(decoded.decode("utf-8"), object_hook=_object_hook)
    except Exception as exc:
        raise ValueError("Invalid backup JSON") from exc
    manifest = envelope.get("manifest") if isinstance(envelope, dict) else None
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        raise ValueError("Backup manifest or payload is missing")
    if manifest.get("format") != FORMAT or payload.get("format") != FORMAT or manifest.get("version") != VERSION or payload.get("version") != VERSION:
        raise ValueError("Unsupported backup format or version")
    if hashlib.sha256(_canonical(payload)).hexdigest() != str(manifest.get("sha256", "")):
        raise ValueError("Backup checksum verification failed")
    collections = payload.get("collections")
    if not isinstance(collections, dict):
        raise ValueError("Backup collections are missing")
    total = 0
    clean: dict[str, list[dict[str, Any]]] = {}
    for name, records in collections.items():
        if not isinstance(name, str) or name.startswith("system.") or name in EXCLUDED:
            raise ValueError(f"Invalid backup collection: {name}")
        if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
            raise ValueError(f"Invalid records in collection {name}")
        total += len(records)
        if total > MAX_RECORDS:
            raise ValueError(f"Backup exceeds {MAX_RECORDS:,} records")
        clean[name] = records
    if int(manifest.get("records", -1)) != total:
        raise ValueError("Backup record count does not match manifest")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if source.get("scope_id") is None:
        raise ValueError("Backup source scope is missing")
    return clean, manifest, source


async def restore_clone_backup(raw: bytes, *, target_scope: int, mode: str) -> dict[str, int]:
    collections, manifest, source = parse_clone_backup(raw)
    source_scope = int(source["scope_id"])
    target_scope = int(target_scope)
    if source_scope == target_scope and mode == "merge":
        # Same-bot merge is safe but intentionally does not overwrite records.
        pass
    db = get_database()
    result = {"records": int(manifest["records"]), "inserted": 0, "existing": 0, "replaced": 0, "skipped": 0}
    for name, records in collections.items():
        transformed = [_transform(d, source_scope, target_scope) for d in records]
        if mode == "replace":
            deleted = await db[name].delete_many({"$or": [{"owner_id": target_scope}, {"data_owner_id": target_scope}]})
            result["replaced"] += int(deleted.deleted_count or 0)
            if transformed:
                # Avoid duplicate _id conflicts and let the collection's normal
                # unique indexes enforce its natural identity.
                try:
                    write = await db[name].insert_many(transformed, ordered=False)
                    result["inserted"] += len(write.inserted_ids)
                except Exception:
                    # Insert individually so one legacy unique/index conflict
                    # cannot prevent the rest of the restore.
                    for doc in transformed:
                        try:
                            await db[name].insert_one(doc)
                            result["inserted"] += 1
                        except Exception:
                            result["skipped"] += 1
            continue

        for doc in transformed:
            query = _identity_query(name, doc)
            if query and "_backup_fingerprint" in query:
                fp = query.pop("_backup_fingerprint")
                query = {"owner_id": target_scope, "$expr": {"$eq": [{"$literal": fp}, fp]}}
                # The fingerprint branch cannot be expressed against an absent
                # field; use a direct small candidate scan below instead.
                candidates = await db[name].find({"owner_id": target_scope}).to_list(length=1000)
                if any(_fingerprint(c) == fp for c in candidates):
                    result["existing"] += 1
                    continue
                query = None
            if query:
                existing = await db[name].find_one(query)
                if existing:
                    result["existing"] += 1
                    continue
            try:
                await db[name].insert_one(doc)
                result["inserted"] += 1
            except Exception:
                result["existing"] += 1
    return result
