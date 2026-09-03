from datetime import datetime, timezone

from database.mongo import get_database

COLLECTION = "sellers"


def sellers_collection():
    return get_database()[COLLECTION]


async def get_seller(owner_id: int):
    return await sellers_collection().find_one({"owner_id": owner_id})


async def create_seller(owner_id: int, first_name=None, username=None):
    now = datetime.now(timezone.utc)

    document = {
        "owner_id": owner_id,
        "first_name": first_name,
        "username": username,
        "active": False,
        "approved": False,
        "suspended": False,
        "plan": None,
        "expiry_date": None,
        "created_at": now,
        "updated_at": now,
    }

    await sellers_collection().insert_one(document)
    return document


async def get_or_create_seller(user):
    seller = await get_seller(user.id)

    if seller:
        return seller

    return await create_seller(
        owner_id=user.id,
        first_name=user.first_name,
        username=user.username,
    )


async def approve_seller(owner_id: int):
    await sellers_collection().update_one(
        {"owner_id": owner_id},
        {
            "$set": {
                "approved": True,
                "active": True,
                "suspended": False,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def suspend_seller(owner_id: int):
    await sellers_collection().update_one(
        {"owner_id": owner_id},
        {
            "$set": {
                "suspended": True,
                "active": False,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def unsuspend_seller(owner_id: int):
    await sellers_collection().update_one(
        {"owner_id": owner_id},
        {
            "$set": {
                "suspended": False,
                "active": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def get_all_sellers():
    return await sellers_collection().find().to_list(length=None)


async def total_sellers():
    return await sellers_collection().count_documents({})

async def find_seller_by_identifier(identifier):
    """Find a seller by Telegram ID or username across all seller-related data.

    Some legacy sellers have a clone bot and platform user record but no document
    in the ``sellers`` collection. Owner search must still find them, then repair
    the missing seller record so future searches and Seller Details work normally.
    """
    import re

    raw = str(identifier or "").strip()
    if not raw:
        return None

    username = raw[1:] if raw.startswith("@") else raw
    username = username.strip().lstrip("@").strip()
    collection = sellers_collection()
    db = get_database()

    async def _repair_missing_seller(owner_id, profile=None):
        try:
            owner_id = int(owner_id)
        except (TypeError, ValueError):
            return None

        existing = await collection.find_one({"owner_id": owner_id})
        if existing:
            return existing

        profile = profile or {}
        now = datetime.now(timezone.utc)
        document = {
            "owner_id": owner_id,
            "first_name": profile.get("first_name") or profile.get("name") or "Unknown",
            "username": profile.get("username") or profile.get("telegram_username"),
            "active": True,
            "approved": bool(profile.get("approved", True)),
            "suspended": False,
            "plan": None,
            "expiry_date": None,
            "created_at": profile.get("created_at") or profile.get("joined_at") or now,
            "updated_at": now,
        }
        await collection.update_one(
            {"owner_id": owner_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        return await collection.find_one({"owner_id": owner_id})

    # 1) Direct seller collection lookup. Support integer/string legacy IDs.
    if raw.lstrip("+").isdigit():
        try:
            numeric_id = int(raw)
        except (TypeError, ValueError):
            numeric_id = None
        if numeric_id is not None:
            id_variants = [numeric_id, str(numeric_id)]
            seller = await collection.find_one({
                "$or": [
                    {"owner_id": {"$in": id_variants}},
                    {"user_id": {"$in": id_variants}},
                    {"seller_id": {"$in": id_variants}},
                    {"telegram_id": {"$in": id_variants}},
                    {"telegram_user_id": {"$in": id_variants}},
                    {"id": {"$in": id_variants}},
                ]
            })
            if seller:
                return seller

            # Legacy/missing seller document: seller_bots is authoritative proof
            # that this Telegram user is a seller.
            bot_record = await db["seller_bots"].find_one({
                "$or": [
                    {"owner_id": {"$in": id_variants}},
                    {"seller_account_id": {"$in": id_variants}},
                ]
            })
            if bot_record:
                profile = await db["users"].find_one({"user_id": numeric_id}) or {}
                return await _repair_missing_seller(numeric_id, profile)

    # 2) Username lookup in seller collection first.
    if username and not any(ch.isspace() for ch in username):
        at_exact = {"$regex": f"^@?{re.escape(username)}$", "$options": "i"}
        seller = await collection.find_one({
            "$or": [
                {"username": at_exact},
                {"username_normalized": {"$regex": f"^{re.escape(username.lower())}$", "$options": "i"}},
                {"telegram_username": at_exact},
                {"user.username": at_exact},
                {"profile.username": at_exact},
            ]
        })
        if seller:
            return seller

        # 3) Fallback through platform users, but only accept it when that user
        # actually owns a clone bot. This prevents ordinary users matching Seller
        # Management search by accident.
        user = await db["users"].find_one({"username": at_exact})
        if user:
            user_id = user.get("user_id")
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                user_id = None
            if user_id is not None:
                bot_record = await db["seller_bots"].find_one({
                    "$or": [
                        {"owner_id": user_id},
                        {"seller_account_id": user_id},
                    ]
                })
                if bot_record:
                    return await _repair_missing_seller(user_id, user)

    return None
