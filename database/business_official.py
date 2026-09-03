"""MongoDB helpers for official Telegram Business bot connections."""

from __future__ import annotations

from datetime import datetime, timezone

from database.mongo import get_database

COLLECTION = "seller_official_business_connections"


def _col():
    return get_database()[COLLECTION]


def _now():
    return datetime.now(timezone.utc)


async def initialize_official_business_indexes() -> None:
    await _col().create_index([("owner_id", 1), ("connection_id", 1)], unique=True)
    await _col().create_index([("owner_id", 1), ("enabled", 1), ("updated_at", -1)])


async def save_official_business_connection(owner_id: int, connection) -> dict:
    """Insert/update one BusinessConnection received from Telegram."""
    user = getattr(connection, "user", None)
    rights = getattr(connection, "rights", None)
    payload = {
        "owner_id": int(owner_id),
        "connection_id": str(connection.id),
        "business_user_id": int(getattr(user, "id", 0) or 0),
        "username": str(getattr(user, "username", "") or ""),
        "first_name": str(getattr(user, "first_name", "") or ""),
        "enabled": bool(getattr(connection, "is_enabled", False)),
        "can_reply": bool(getattr(rights, "can_reply", False)) if rights else True,
        "updated_at": _now(),
    }
    await _col().update_one(
        {"owner_id": int(owner_id), "connection_id": str(connection.id)},
        {
            "$set": payload,
            "$setOnInsert": {
                "created_at": _now(),
                "conversations": 0,
                "welcome_sent": 0,
                "auto_replies_sent": 0,
            },
        },
        upsert=True,
    )
    return await _col().find_one(
        {"owner_id": int(owner_id), "connection_id": str(connection.id)},
        {"_id": 0},
    )


async def get_official_business_connection(owner_id: int, connection_id: str) -> dict | None:
    return await _col().find_one(
        {"owner_id": int(owner_id), "connection_id": str(connection_id)},
        {"_id": 0},
    )


async def increment_official_business_stat(
    owner_id: int,
    connection_id: str,
    field: str,
    amount: int = 1,
) -> bool:
    allowed = {"conversations", "welcome_sent", "auto_replies_sent", "templates_used", "plans_opened", "renew_opened", "profile_opened", "referral_opened"}
    if field not in allowed:
        raise ValueError("Unsupported official business statistic")
    result = await _col().update_one(
        {"owner_id": int(owner_id), "connection_id": str(connection_id)},
        {"$inc": {field: int(amount)}, "$set": {"updated_at": _now()}},
    )
    return bool(result.matched_count)


async def count_active_official_business_connections(owner_id: int) -> int:
    """Return the number of currently enabled official Telegram Business connections."""
    return int(await _col().count_documents({
        "owner_id": int(owner_id),
        "enabled": True,
    }))
