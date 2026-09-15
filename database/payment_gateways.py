from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from database.mongo import get_database
from utils.crypto import decrypt_secret, encrypt_secret

CONFIGS = "payment_gateway_configs"
TRANSACTIONS = "gateway_transactions"
EVENTS = "gateway_webhook_events"
RAZORPAY_QR_POOL = "razorpay_qr_pool"

SUPPORTED_GATEWAYS = ("razorpay", "cashfree")
SECRET_FIELDS = {
    "razorpay": {"key_secret", "webhook_secret"},
    "cashfree": {"client_secret"},
}
REQUIRED_FIELDS = {
    "razorpay": ("key_id", "key_secret", "webhook_secret"),
    "cashfree": ("client_id", "client_secret"),
}
VALID_MODES = {"test", "live"}


def gateway_missing_fields(gateway: str, settings: dict | None) -> list[str]:
    gateway = gateway.lower().strip()
    settings = settings or {}
    return [field for field in REQUIRED_FIELDS.get(gateway, ()) if not str(settings.get(field, "")).strip()]


def gateway_is_ready(gateway: str, settings: dict | None) -> bool:
    return gateway in SUPPORTED_GATEWAYS and not gateway_missing_fields(gateway, settings)


def _configs():
    return get_database()[CONFIGS]


def _transactions():
    return get_database()[TRANSACTIONS]


def _events():
    return get_database()[EVENTS]


def _razorpay_qr_pool():
    return get_database()[RAZORPAY_QR_POOL]


async def initialize_payment_gateway_indexes():
    await _configs().create_index([("scope", 1), ("owner_id", 1)], unique=True)
    await _transactions().create_index("transaction_id", unique=True)
    await _transactions().create_index([("gateway", 1), ("gateway_order_id", 1)])
    await _transactions().create_index([("scope", 1), ("owner_id", 1), ("created_at", -1)])
    await _transactions().create_index([("status", 1), ("updated_at", -1)])
    await _transactions().create_index([("gateway", 1), ("status", 1), ("next_gateway_recovery_at", 1)])
    await _events().create_index([("gateway", 1), ("event_key", 1)], unique=True)
    await _events().create_index("created_at", expireAfterSeconds=30 * 24 * 60 * 60)
    await _transactions().create_index([("status", 1), ("fulfillment_lease_until", 1)])
    await _transactions().create_index([("gateway_payment_id", 1)], sparse=True)
    await _razorpay_qr_pool().create_index("qr_code_id", unique=True)
    await _razorpay_qr_pool().create_index([("owner_id", 1), ("plan_id", 1), ("status", 1), ("qr_close_by", 1)])


async def get_gateway_config(scope: str, owner_id: int = 0, decrypt: bool = False) -> dict:
    doc = await _configs().find_one({"scope": scope, "owner_id": int(owner_id)}) or {
        "scope": scope,
        "owner_id": int(owner_id),
        "default_gateway": "manual",
        "manual_enabled": True,
        "stars_enabled": False,
        "gateways": {},
    }
    if not decrypt:
        return doc
    result = dict(doc)
    gateways = {}
    for name, cfg in (doc.get("gateways") or {}).items():
        item = dict(cfg)
        for key in SECRET_FIELDS.get(name, set()):
            value = item.get(key)
            if value:
                try:
                    item[key] = decrypt_secret(value)
                except Exception:
                    item[key] = ""
        gateways[name] = item
    result["gateways"] = gateways
    return result


async def save_gateway_config(
    scope: str,
    owner_id: int,
    gateway: str,
    values: dict,
) -> dict:
    gateway = gateway.lower().strip()
    if gateway not in SUPPORTED_GATEWAYS:
        raise ValueError("Unsupported gateway")
    current = await get_gateway_config(scope, owner_id, decrypt=True)
    item = dict((current.get("gateways") or {}).get(gateway) or {})
    for key, value in values.items():
        if isinstance(value, str):
            value = value.strip()
        if key == "mode":
            # Automatic gateways always operate in live mode.
            value = "live"
        item[key] = value

    item["mode"] = "live"

    if item.get("enabled") and not gateway_is_ready(gateway, item):
        missing = ", ".join(gateway_missing_fields(gateway, item))
        raise ValueError(f"Set required credentials first: {missing}")

    encrypted = dict(item)
    for key in SECRET_FIELDS.get(gateway, set()):
        if encrypted.get(key):
            encrypted[key] = encrypt_secret(str(encrypted[key]))
    now = datetime.now(timezone.utc)
    await _configs().update_one(
        {"scope": scope, "owner_id": int(owner_id)},
        {
            "$set": {
                f"gateways.{gateway}": encrypted,
                "updated_at": now,
            },
            "$setOnInsert": {
                "scope": scope,
                "owner_id": int(owner_id),
                "default_gateway": "manual",
                "manual_enabled": True,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await get_gateway_config(scope, owner_id, decrypt=True)


async def set_gateway_preferences(
    scope: str,
    owner_id: int,
    *,
    default_gateway: str | None = None,
    manual_enabled: bool | None = None,
    stars_enabled: bool | None = None,
):
    updates = {"updated_at": datetime.now(timezone.utc)}
    if default_gateway is not None:
        if default_gateway != "manual" and default_gateway not in SUPPORTED_GATEWAYS:
            raise ValueError("Unsupported default gateway")
        updates["default_gateway"] = default_gateway
    if manual_enabled is not None:
        updates["manual_enabled"] = bool(manual_enabled)
    if stars_enabled is not None:
        updates["stars_enabled"] = bool(stars_enabled)
    await _configs().update_one(
        {"scope": scope, "owner_id": int(owner_id)},
        {"$set": updates, "$setOnInsert": {"scope": scope, "owner_id": int(owner_id), "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return await get_gateway_config(scope, owner_id, decrypt=True)


async def mark_valid_webhook_received(scope: str, owner_id: int, gateway: str) -> None:
    """Record the latest successfully authenticated webhook for setup testing."""
    await _configs().update_one(
        {"scope": scope, "owner_id": int(owner_id)},
        {
            "$set": {
                f"gateways.{gateway}.last_webhook_received_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "scope": scope,
                "owner_id": int(owner_id),
                "default_gateway": "manual",
                "manual_enabled": True,
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


async def enabled_gateways(scope: str, owner_id: int = 0) -> list[str]:
    cfg = await get_gateway_config(scope, owner_id, decrypt=True)
    gateways = cfg.get("gateways") or {}
    return [
        name
        for name in SUPPORTED_GATEWAYS
        if gateways.get(name, {}).get("enabled") and gateway_is_ready(name, gateways.get(name))
    ]


async def create_gateway_transaction(
    *,
    scope: str,
    owner_id: int,
    payer_user_id: int,
    gateway: str,
    amount: float,
    currency: str,
    purpose: str,
    reference_id: str,
    metadata: dict,
) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "transaction_id": "gtw_" + uuid4().hex[:20],
        "scope": scope,
        "owner_id": int(owner_id),
        "payer_user_id": int(payer_user_id),
        "gateway": gateway,
        "amount": float(amount),
        "currency": currency.upper(),
        "purpose": purpose,
        "reference_id": str(reference_id),
        "metadata": metadata or {},
        "status": "created",
        "created_at": now,
        "updated_at": now,
    }
    await _transactions().insert_one(doc)
    return doc


async def update_gateway_transaction(transaction_id: str, **values) -> dict | None:
    values["updated_at"] = datetime.now(timezone.utc)
    return await _transactions().find_one_and_update(
        {"transaction_id": transaction_id},
        {"$set": values},
        return_document=ReturnDocument.AFTER,
    )


async def get_gateway_transaction(transaction_id: str) -> dict | None:
    return await _transactions().find_one({"transaction_id": transaction_id})


async def get_transaction_by_gateway_order(gateway: str, gateway_order_id: str) -> dict | None:
    return await _transactions().find_one({"gateway": gateway, "gateway_order_id": str(gateway_order_id)})


async def claim_transaction_success(transaction_id: str, gateway_payment_id: str, raw_event: dict | None = None) -> dict | None:
    now = datetime.now(timezone.utc)
    return await _transactions().find_one_and_update(
        {"transaction_id": transaction_id, "status": {"$nin": ["paid", "fulfilled"]}},
        {"$set": {"status": "paid", "gateway_payment_id": str(gateway_payment_id or ""), "paid_at": now, "raw_success": raw_event or {}, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def claim_transaction_fulfillment(
    transaction_id: str,
    *,
    lease_seconds: int = 300,
) -> dict | None:
    """Claim exclusive fulfillment ownership for one paid transaction.

    A short lease allows recovery after a process crash while preventing two
    webhook workers from activating the same purchase at the same time.
    """
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
    return await _transactions().find_one_and_update(
        {
            "transaction_id": transaction_id,
            "fulfilled_at": {"$exists": False},
            "$or": [
                {"status": {"$in": ["paid", "paid_unfulfilled"]}},
                {
                    "status": "fulfilling",
                    "fulfillment_lease_until": {"$lte": now},
                },
            ],
        },
        {
            "$set": {
                "status": "fulfilling",
                "fulfillment_started_at": now,
                "fulfillment_lease_until": lease_until,
                "updated_at": now,
            },
            "$inc": {"fulfillment_attempts": 1},
            "$unset": {"fulfillment_error": ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def mark_transaction_fulfilled(
    transaction_id: str,
    details: dict | None = None,
) -> dict | None:
    now = datetime.now(timezone.utc)
    return await _transactions().find_one_and_update(
        {"transaction_id": transaction_id, "status": "fulfilling"},
        {
            "$set": {
                "status": "fulfilled",
                "fulfilled_at": now,
                "fulfillment": details or {},
                "updated_at": now,
            },
            "$unset": {"fulfillment_lease_until": ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def mark_transaction_fulfillment_retry(
    transaction_id: str,
    reason: str,
) -> dict | None:
    """Release a failed fulfillment claim so a later webhook can retry it."""
    now = datetime.now(timezone.utc)
    return await _transactions().find_one_and_update(
        {"transaction_id": transaction_id, "status": "fulfilling"},
        {
            "$set": {
                "status": "paid_unfulfilled",
                "fulfillment_error": str(reason)[:500],
                "updated_at": now,
            },
            "$unset": {"fulfillment_lease_until": ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def mark_transaction_failed(transaction_id: str, reason: str = "", raw_event: dict | None = None):
    return await update_gateway_transaction(transaction_id, status="failed", failure_reason=reason[:500], raw_failure=raw_event or {})


async def reserve_webhook_event(
    gateway: str,
    event_key: str,
    payload: dict | None = None,
) -> bool:
    try:
        await _events().insert_one({
            "gateway": gateway,
            "event_key": str(event_key),
            "payload": payload or {},
            "created_at": datetime.now(timezone.utc),
        })
        return True
    except DuplicateKeyError:
        return False


async def claim_transaction_notification(transaction_id: str, notification: str) -> bool:
    """Atomically reserve a one-time post-payment notification/delivery.

    This is a second idempotency barrier for side effects such as subscriber
    invite delivery and seller notifications. It prevents duplicate messages
    even if the fulfillment function is reached twice by webhook/callback races.
    """
    name = str(notification or "").strip().lower()
    if not name.replace("_", "").isalnum():
        raise ValueError("Invalid notification name")
    field = f"notifications.{name}.claimed_at"
    now = datetime.now(timezone.utc)
    result = await _transactions().update_one(
        {
            "transaction_id": str(transaction_id),
            field: {"$exists": False},
        },
        {
            "$set": {
                field: now,
                f"notifications.{name}.status": "claimed",
                "updated_at": now,
            }
        },
    )
    return result.modified_count == 1


async def complete_transaction_notification(transaction_id: str, notification: str, details: dict | None = None) -> None:
    name = str(notification or "").strip().lower()
    now = datetime.now(timezone.utc)
    await _transactions().update_one(
        {"transaction_id": str(transaction_id)},
        {
            "$set": {
                f"notifications.{name}.status": "sent",
                f"notifications.{name}.sent_at": now,
                f"notifications.{name}.details": details or {},
                "updated_at": now,
            }
        },
    )


async def fail_transaction_notification(transaction_id: str, notification: str, error: str) -> None:
    name = str(notification or "").strip().lower()
    now = datetime.now(timezone.utc)
    await _transactions().update_one(
        {"transaction_id": str(transaction_id)},
        {
            "$set": {
                f"notifications.{name}.status": "failed",
                f"notifications.{name}.error": str(error)[:500],
                "updated_at": now,
            }
        },
    )


async def recoverable_failed_notifications(limit: int = 100) -> list[dict]:
    """Return fulfilled child-subscription transactions whose invite delivery failed.

    Payment fulfillment and subscription activation are already complete for these
    records. Recovery must retry only the subscriber access message, never charge,
    extend validity, or create another invoice.
    """
    query = {
        "purpose": "child_subscription",
        "status": "fulfilled",
        "fulfilled_at": {"$exists": True},
        "notifications.subscriber_access.status": "failed",
    }
    safe_limit = max(1, min(int(limit), 500))
    return await _transactions().find(query).sort("updated_at", 1).limit(safe_limit).to_list(length=safe_limit)


async def reclaim_failed_transaction_notification(transaction_id: str, notification: str) -> bool:
    """Atomically reclaim one failed notification for a retry worker."""
    name = str(notification or "").strip().lower()
    if not name.replace("_", "").isalnum():
        raise ValueError("Invalid notification name")
    now = datetime.now(timezone.utc)
    status_field = f"notifications.{name}.status"
    result = await _transactions().update_one(
        {
            "transaction_id": str(transaction_id),
            status_field: "failed",
        },
        {
            "$set": {
                status_field: "claimed",
                f"notifications.{name}.claimed_at": now,
                "updated_at": now,
            },
            "$inc": {f"notifications.{name}.retry_count": 1},
            "$unset": {f"notifications.{name}.error": ""},
        },
    )
    return result.modified_count == 1


async def gateway_history(scope: str, owner_id: int, limit: int = 50) -> list[dict]:
    return await _transactions().find({"scope": scope, "owner_id": int(owner_id)}).sort("created_at", -1).to_list(length=limit)


async def expire_stale_cashfree_transactions(max_age_minutes: int = 30) -> int:
    """Stop abandoned Cashfree orders from being retried forever.

    Cashfree webhooks/return callbacks remain authoritative while an order is
    active. This only marks very old, still-unpaid local transactions as
    expired so the recovery worker cannot repeatedly scan historical rows.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max(1, int(max_age_minutes)))
    result = await _transactions().update_many(
        {
            "gateway": "cashfree",
            "status": {"$in": ["pending", "verification_pending"]},
            "fulfilled_at": {"$exists": False},
            "created_at": {"$lte": cutoff},
        },
        {
            "$set": {
                "status": "expired",
                "verification_error": "Cashfree payment was not completed before recovery timeout",
                "expired_at": now,
                "updated_at": now,
            },
            "$unset": {"next_gateway_recovery_at": ""},
        },
    )
    return int(result.modified_count or 0)


async def defer_cashfree_verification(
    transaction_id: str,
    reason: str,
    *,
    retry_after_seconds: int = 120,
) -> dict | None:
    """Persist a backoff so one unpaid order is not queried every job cycle."""
    now = datetime.now(timezone.utc)
    retry_after = now + timedelta(seconds=max(30, int(retry_after_seconds)))
    return await _transactions().find_one_and_update(
        {
            "transaction_id": str(transaction_id),
            "status": {"$in": ["pending", "verification_pending"]},
        },
        {
            "$set": {
                "status": "verification_pending",
                "verification_error": str(reason)[:500],
                "last_gateway_recovery_at": now,
                "next_gateway_recovery_at": retry_after,
                "updated_at": now,
            },
            "$inc": {"gateway_recovery_attempts": 1},
        },
        return_document=ReturnDocument.AFTER,
    )


async def recoverable_gateway_transactions(limit: int = 100) -> list[dict]:
    """Return paid work and due Cashfree verifications without retry storms."""
    now = datetime.now(timezone.utc)
    query = {
        "fulfilled_at": {"$exists": False},
        "$or": [
            {"status": {"$in": ["paid", "paid_unfulfilled"]}},
            {"status": "fulfilling", "fulfillment_lease_until": {"$lte": now}},
            {
                "gateway": "cashfree",
                "status": {"$in": ["pending", "verification_pending"]},
                "$or": [
                    {"next_gateway_recovery_at": {"$exists": False}},
                    {"next_gateway_recovery_at": {"$lte": now}},
                ],
            },
        ],
    }
    size = max(1, min(int(limit), 500))
    return await _transactions().find(query).sort("updated_at", 1).limit(size).to_list(length=size)


async def expire_due_razorpay_qr_transactions(limit: int = 100) -> list[dict]:
    """Claim Razorpay UPI QR transactions whose configured close time has passed.

    Only unpaid local transactions are returned. Payment webhooks remain
    authoritative; a transaction that was already marked paid/fulfilled is
    never expired here.
    """
    now = datetime.now(timezone.utc)
    rows = await _transactions().find({
        "gateway": "razorpay",
        "status": {"$in": ["created", "pending", "verification_pending"]},
        "checkout_mode": "upi_qr",
        "qr_close_by": {"$lte": int(now.timestamp())},
        "fulfilled_at": {"$exists": False},
    }).sort("qr_close_by", 1).limit(max(1, min(int(limit), 500))).to_list(length=max(1, min(int(limit), 500)))
    return rows


async def mark_transaction_expired(transaction_id: str) -> dict | None:
    now = datetime.now(timezone.utc)
    return await _transactions().find_one_and_update(
        {
            "transaction_id": str(transaction_id),
            "status": {"$in": ["created", "pending", "verification_pending"]},
            "fulfilled_at": {"$exists": False},
        },
        {
            "$set": {
                "status": "expired",
                "expired_at": now,
                "failure_reason": "Razorpay UPI QR expired before payment",
                "updated_at": now,
            },
            "$unset": {"next_gateway_recovery_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def list_razorpay_qr_pool_owners() -> list[int]:
    """Return seller owners whose Razorpay gateway is enabled in UPI QR mode."""
    rows = await _configs().find(
        {
            "scope": "seller",
            "gateways.razorpay.enabled": True,
            "gateways.razorpay.checkout_mode": "upi_qr",
        },
        {"owner_id": 1},
    ).to_list(length=5000)
    return [int(row.get("owner_id")) for row in rows if row.get("owner_id") is not None]


async def count_available_razorpay_qr_pool(
    owner_id: int, plan_id: str, amount: float, currency: str = "INR", *, minimum_valid_seconds: int = 90
) -> int:
    cutoff = int(datetime.now(timezone.utc).timestamp()) + max(0, int(minimum_valid_seconds))
    return int(await _razorpay_qr_pool().count_documents({
        "owner_id": int(owner_id),
        "plan_id": str(plan_id),
        "amount": float(amount),
        "currency": str(currency).upper(),
        "status": "available",
        "qr_close_by": {"$gt": cutoff},
    }))


async def create_razorpay_qr_pool_entry(
    *, owner_id: int, plan_id: str, amount: float, currency: str, qr_code_id: str,
    image_url: str = "", image_content: str = "", telegram_file_id: str = "", qr_close_by: int, gateway_response: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "owner_id": int(owner_id),
        "plan_id": str(plan_id),
        "amount": float(amount),
        "currency": str(currency).upper(),
        "qr_code_id": str(qr_code_id),
        "image_url": str(image_url or ""),
        "image_content": str(image_content or ""),
        "telegram_file_id": str(telegram_file_id or ""),
        "qr_close_by": int(qr_close_by),
        "status": "available",
        "created_at": now,
        "updated_at": now,
        "gateway_response": gateway_response or {},
    }
    try:
        await _razorpay_qr_pool().insert_one(doc)
    except DuplicateKeyError:
        existing = await _razorpay_qr_pool().find_one({"qr_code_id": str(qr_code_id)})
        return existing or doc
    return doc


async def claim_razorpay_qr_pool_entry(
    owner_id: int, plan_id: str, amount: float, currency: str, transaction_id: str
) -> dict | None:
    now = int(datetime.now(timezone.utc).timestamp()) + 30
    return await _razorpay_qr_pool().find_one_and_update(
        {
            "owner_id": int(owner_id),
            "plan_id": str(plan_id),
            "amount": float(amount),
            "currency": str(currency).upper(),
            "status": "available",
            "qr_close_by": {"$gt": now},
        },
        {"$set": {
            "status": "assigned",
            "transaction_id": str(transaction_id),
            "assigned_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }},
        sort=[("qr_close_by", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def cache_razorpay_qr_telegram_file_id(qr_code_id: str, telegram_file_id: str) -> dict | None:
    """Cache the Telegram file_id after the QR image has been uploaded once.

    Telegram file_ids are reusable by the same bot, so subsequent users can
    receive the already-uploaded QR without another image upload.
    """
    if not str(qr_code_id).strip() or not str(telegram_file_id).strip():
        return None
    return await _razorpay_qr_pool().find_one_and_update(
        {"qr_code_id": str(qr_code_id)},
        {"$set": {"telegram_file_id": str(telegram_file_id), "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


async def get_expired_razorpay_qr_pool_entries(limit: int = 500) -> list[dict]:
    now = int(datetime.now(timezone.utc).timestamp())
    size = max(1, min(int(limit), 1000))
    return await _razorpay_qr_pool().find(
        {"qr_close_by": {"$lte": now}}
    ).sort("qr_close_by", 1).limit(size).to_list(length=size)


async def delete_razorpay_qr_pool_entry(qr_code_id: str) -> int:
    result = await _razorpay_qr_pool().delete_one({"qr_code_id": str(qr_code_id)})
    return int(result.deleted_count or 0)


async def delete_razorpay_qr_pool_entries_for_plan(owner_id: int, plan_id: str) -> int:
    result = await _razorpay_qr_pool().delete_many({"owner_id": int(owner_id), "plan_id": str(plan_id), "status": "available"})
    return int(result.deleted_count or 0)


async def gateway_transaction_stats(scope: str, owner_id: int) -> dict:
    pipeline = [
        {"$match": {"scope": scope, "owner_id": int(owner_id)}},
        {"$group": {
            "_id": {"gateway": "$gateway", "status": "$status"},
            "count": {"$sum": 1},
            "amount": {"$sum": "$amount"},
        }},
    ]
    rows = await _transactions().aggregate(pipeline).to_list(length=None)
    result: dict[str, dict] = {}
    for row in rows:
        key = row.get("_id") or {}
        gateway = str(key.get("gateway") or "unknown")
        status = str(key.get("status") or "unknown")
        bucket = result.setdefault(gateway, {"total": 0, "amount": 0.0, "statuses": {}})
        count = int(row.get("count") or 0)
        amount = float(row.get("amount") or 0)
        bucket["total"] += count
        bucket["amount"] += amount
        bucket["statuses"][status] = {"count": count, "amount": amount}
    return result
