from datetime import datetime, timezone

from database.mongo import get_database
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

COLLECTION = "users"


def users_collection():
    return get_database()[COLLECTION]


async def get_user(user_id: int):
    return await users_collection().find_one({"user_id": user_id})


async def get_user_by_username(username: str):
    username = username.replace("@", "").strip()

    return await users_collection().find_one({
        "username": {"$regex": f"^{username}$", "$options": "i"}
    })


async def create_user(user):
    now = datetime.now(timezone.utc)

    document = {
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "is_bot": user.is_bot,
        "language_code": user.language_code,
        "banned": False,
        "ban_reason": None,
        "banned_at": None,
        "joined_at": now,
        "created_at": now,
        "updated_at": now,
    }

    await users_collection().insert_one(document)
    return document


async def get_or_create_user(user):
    """Create or refresh a user with one atomic MongoDB round trip.

    The previous implementation performed up to three sequential queries on
    every /start, which made the first response and following callbacks feel
    slow on hosted MongoDB connections.
    """
    now = datetime.now(timezone.utc)
    update = {
        "$set": {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "is_bot": user.is_bot,
            "language_code": user.language_code,
            "updated_at": now,
        },
        "$setOnInsert": {
            "user_id": user.id,
            "banned": False,
            "ban_reason": None,
            "banned_at": None,
            "joined_at": now,
            "created_at": now,
        },
    }
    try:
        return await users_collection().find_one_and_update(
            {"user_id": user.id},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # A simultaneous update may win the upsert race. Fetch the record
        # instead of making the user retry /start.
        return await get_user(user.id)


async def update_user(user_id: int, data: dict):
    data["updated_at"] = datetime.now(timezone.utc)

    await users_collection().update_one(
        {"user_id": user_id},
        {"$set": data},
    )


async def ban_user(user_id: int, reason: str = "No reason provided"):
    await update_user(
        user_id,
        {
            "banned": True,
            "ban_reason": reason,
            "banned_at": datetime.now(timezone.utc),
        },
    )


async def unban_user(user_id: int):
    await update_user(
        user_id,
        {
            "banned": False,
            "ban_reason": None,
            "banned_at": None,
        },
    )


async def is_user_banned(user_id: int):
    user = await get_user(user_id)
    return bool(user and user.get("banned"))


async def total_users():
    return await users_collection().count_documents({})
