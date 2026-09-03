from datetime import datetime, timedelta, timezone

from database.mongo import get_database

COLLECTION = "clone_referral_unlocks"


def _now():
    return datetime.now(timezone.utc)


async def get_referral_unlock(owner_id: int, user_id: int):
    return await get_database()[COLLECTION].find_one({
        "owner_id": int(owner_id),
        "user_id": int(user_id),
    })


async def save_referral_unlock(
    owner_id: int,
    user_id: int,
    chat_id: int,
    invite_link: str,
    duration_days: int,
):
    now = _now()
    expires_at = now + timedelta(days=max(1, int(duration_days)))
    await get_database()[COLLECTION].update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$set": {
                "chat_id": int(chat_id),
                "invite_link": str(invite_link),
                "duration_days": max(1, int(duration_days)),
                "unlocked": True,
                "active": True,
                "expires_at": expires_at,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return await get_referral_unlock(owner_id, user_id)


async def expired_referral_unlocks(owner_id: int, limit: int = 200):
    return await get_database()[COLLECTION].find({
        "owner_id": int(owner_id),
        "active": True,
        "expires_at": {"$lte": _now()},
    }).to_list(length=max(1, int(limit)))


async def mark_referral_unlock_expired(owner_id: int, user_id: int):
    now = _now()
    result = await get_database()[COLLECTION].update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id), "active": True},
        {"$set": {"active": False, "expired_at": now, "updated_at": now}},
    )
    return result.modified_count > 0
