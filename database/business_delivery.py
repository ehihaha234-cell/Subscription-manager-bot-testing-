"""Business Automation payment-delivery routing and diagnostics.

Official Telegram Business chats are the primary delivery route. Legacy
Normal Account routes are still readable for backward compatibility, but new
payment deliveries prefer active Official Business recipient records matched
by Telegram numeric user/chat ID.
"""

from __future__ import annotations

from datetime import datetime, timezone

from database.mongo import get_database

COLLECTION = "business_automation_contact_routes"
OFFICIAL_RECIPIENT_COLLECTION = "business_automation_business_recipients"
OFFICIAL_CONNECTION_COLLECTION = "seller_official_business_connections"
DELIVERY_LOG_COLLECTION = "business_payment_delivery_logs"


def _col(name: str = COLLECTION):
    return get_database()[name]


def _now():
    return datetime.now(timezone.utc)


async def record_business_contact(
    owner_id: int,
    user_id: int,
    *,
    mode: str,
    account_user_id: int = 0,
    connection_id: str = "",
    chat_id: int = 0,
) -> None:
    """Upsert the latest route for one subscriber and connected account."""
    mode = str(mode or "").strip().lower()
    if mode not in {"normal", "official"}:
        return
    key = {
        "owner_id": int(owner_id),
        "user_id": int(user_id),
        "mode": mode,
        "account_user_id": int(account_user_id or 0),
        "connection_id": str(connection_id or ""),
    }
    await _col().update_one(
        key,
        {
            "$set": {
                **key,
                "chat_id": int(chat_id or user_id),
                "active": True,
                "updated_at": _now(),
            },
            "$setOnInsert": {"created_at": _now()},
        },
        upsert=True,
    )


async def list_business_contact_routes(owner_id: int, user_id: int) -> list[dict]:
    """Return deduplicated payment-delivery routes for a subscriber.

    The Official Business recipient table is authoritative because it is
    updated on every incoming Business message. Legacy contact-route records
    are appended as fallback for older conversations.
    """
    owner_id = int(owner_id)
    user_id = int(user_id)
    routes: list[dict] = []
    seen: set[tuple[str, str, int]] = set()

    # In a private Telegram Business conversation chat_id equals the customer's
    # numeric Telegram ID. user_id is also stored explicitly by the new patch.
    recipient_query = {
        "owner_id": owner_id,
        "active": {"$ne": False},
        "$or": [{"user_id": user_id}, {"chat_id": user_id}],
    }
    recipients = await _col(OFFICIAL_RECIPIENT_COLLECTION).find(
        recipient_query, {"_id": 0}
    ).sort("last_seen_at", -1).to_list(length=20)

    for recipient in recipients:
        connection_id = str(recipient.get("connection_id") or "")
        chat_id = int(recipient.get("chat_id") or user_id)
        if not connection_id:
            continue
        connection = await _col(OFFICIAL_CONNECTION_COLLECTION).find_one(
            {"owner_id": owner_id, "connection_id": connection_id},
            {"_id": 0, "enabled": 1, "can_reply": 1, "business_user_id": 1},
        )
        if connection and (
            connection.get("enabled") is False or connection.get("can_reply") is False
        ):
            continue
        key = ("official", connection_id, chat_id)
        if key in seen:
            continue
        seen.add(key)
        routes.append({
            "owner_id": owner_id,
            "user_id": user_id,
            "mode": "official",
            "connection_id": connection_id,
            "chat_id": chat_id,
            "account_user_id": int((connection or {}).get("business_user_id") or 0),
            "source": "official_recipient",
            "updated_at": recipient.get("last_seen_at") or recipient.get("updated_at"),
        })

    legacy = await _col().find(
        {"owner_id": owner_id, "user_id": user_id, "active": {"$ne": False}},
        {"_id": 0},
    ).sort("updated_at", -1).to_list(length=20)
    for route in legacy:
        mode = str(route.get("mode") or "")
        connection_id = str(route.get("connection_id") or "")
        chat_id = int(route.get("chat_id") or user_id)
        key = (mode, connection_id, chat_id)
        if key in seen:
            continue
        seen.add(key)
        routes.append(route)

    return routes


async def log_business_payment_delivery(
    owner_id: int,
    user_id: int,
    *,
    bot_status: str = "not_attempted",
    business_status: str = "not_attempted",
    start_button_status: str = "not_attempted",
    business_reason: str = "",
    routes_found: int = 0,
    transaction_id: str = "",
) -> None:
    """Store the latest delivery result for support/debugging."""
    await _col(DELIVERY_LOG_COLLECTION).insert_one({
        "owner_id": int(owner_id),
        "user_id": int(user_id),
        "transaction_id": str(transaction_id or ""),
        "bot_status": str(bot_status or ""),
        "business_status": str(business_status or ""),
        "start_button_status": str(start_button_status or ""),
        "business_reason": str(business_reason or "")[:500],
        "routes_found": int(routes_found or 0),
        "created_at": _now(),
    })
