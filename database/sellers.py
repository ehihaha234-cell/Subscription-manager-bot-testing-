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
    """Return the complete seller registry without losing legacy sellers.

    Seller Management must not depend only on the current ``sellers`` rows.
    A seller remains a seller after a clone bot is removed/expired, and older
    databases can have a clone/trial record without a corresponding seller
    document.  Reconcile the registry from all durable seller signals:
    - existing seller documents
    - every clone owner (including removed clone records)
    - seller plan assignments (including free trials)
    - seller subscription payment records

    Main-bot users are deliberately NOT added here; they are searchable, but
    only users with seller activity are shown/count as sellers.
    """
    collection = sellers_collection()
    db = get_database()

    existing = await collection.find().to_list(length=None)
    by_id = {}
    for seller in existing:
        try:
            sid = int(seller.get("owner_id"))
        except (TypeError, ValueError):
            continue
        by_id[sid] = seller

    candidate_ids = set(by_id)

    # A clone remains historical seller evidence even after it is marked
    # removed, so do not filter on active/status here.
    bot_rows = await db["seller_bots"].find(
        {}, {"owner_id": 1, "seller_account_id": 1, "seller_id": 1}
    ).to_list(length=None)
    for row in bot_rows:
        for key in ("owner_id", "seller_account_id", "seller_id"):
            try:
                value = int(row.get(key))
            except (TypeError, ValueError):
                value = 0
            if value:
                candidate_ids.add(value)

    # A trial or paid seller plan is also durable seller evidence.
    assignment_rows = await db["seller_plan_assignments"].find(
        {}, {"owner_id": 1}
    ).to_list(length=None)
    for row in assignment_rows:
        try:
            value = int(row.get("owner_id"))
        except (TypeError, ValueError):
            value = 0
        if value:
            candidate_ids.add(value)

    payment_rows = await db["seller_subscription_payments"].find(
        {}, {"seller_id": 1}
    ).to_list(length=None)
    for row in payment_rows:
        try:
            value = int(row.get("seller_id"))
        except (TypeError, ValueError):
            value = 0
        if value:
            candidate_ids.add(value)

    missing_ids = [sid for sid in candidate_ids if sid not in by_id]
    if missing_ids:
        profiles = await db["users"].find(
            {"user_id": {"$in": missing_ids}},
            {"user_id": 1, "first_name": 1, "last_name": 1, "username": 1,
             "created_at": 1, "joined_at": 1, "updated_at": 1},
        ).to_list(length=None)
        profile_by_id = {}
        for profile in profiles:
            try:
                profile_by_id[int(profile.get("user_id"))] = profile
            except (TypeError, ValueError):
                pass

        now = datetime.now(timezone.utc)
        for sid in missing_ids:
            profile = profile_by_id.get(sid, {})
            document = {
                "owner_id": sid,
                "first_name": profile.get("first_name") or profile.get("name") or "Unknown",
                "username": profile.get("username") or profile.get("telegram_username"),
                "active": True,
                "approved": True,
                "suspended": False,
                "plan": None,
                "expiry_date": None,
                "created_at": profile.get("created_at") or profile.get("joined_at") or now,
                "updated_at": profile.get("updated_at") or now,
            }
            await collection.update_one(
                {"owner_id": sid},
                {"$setOnInsert": document},
                upsert=True,
            )
            by_id[sid] = await collection.find_one({"owner_id": sid}) or document

    return list(by_id.values())


async def total_sellers():
    # Keep the dashboard count in sync with the same reconciled registry used
    # by Seller List, so a legacy/missing seller record cannot make the count
    # smaller than the actual seller population.
    return len(await get_all_sellers())

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
    # If the numeric identifier is actually a clone Bot ID, resolve its owner/seller.
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

            # Clone Bot ID lookup. A seller can therefore be found by any of
            # their registered clone Bot IDs from Owner > Seller Management.
            bot_record = await db["seller_bots"].find_one({"bot_id": {"$in": id_variants}})
            if bot_record:
                owner_candidate = (
                    bot_record.get("owner_id")
                    or bot_record.get("seller_account_id")
                    or bot_record.get("seller_id")
                )
                try:
                    owner_candidate = int(owner_candidate)
                except (TypeError, ValueError):
                    owner_candidate = None
                if owner_candidate is not None:
                    seller = await collection.find_one({"owner_id": owner_candidate})
                    if seller:
                        return seller
                    profile = await db["users"].find_one({"user_id": owner_candidate}) or {}
                    return await _repair_missing_seller(owner_candidate, profile)

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

        # 3) Clone Bot username lookup. This lets Owner search a seller by any
        # registered clone bot username, regardless of which bot belongs to them.
        bot_record = await db["seller_bots"].find_one({
            "$or": [
                {"bot_username_normalized": username.lower()},
                {"bot_username": at_exact},
            ]
        })
        if bot_record:
            owner_candidate = (
                bot_record.get("owner_id")
                or bot_record.get("seller_account_id")
                or bot_record.get("seller_id")
            )
            try:
                owner_candidate = int(owner_candidate)
            except (TypeError, ValueError):
                owner_candidate = None
            if owner_candidate is not None:
                seller = await collection.find_one({"owner_id": owner_candidate})
                if seller:
                    return seller
                profile = await db["users"].find_one({"user_id": owner_candidate}) or {}
                return await _repair_missing_seller(owner_candidate, profile)

        # 4) Fallback through platform users, but only accept it when that user
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

    # 5) Any user who has started the Main Bot is searchable in Seller
    # Management, even when they never connected a clone bot or their old bot
    # was removed/expired. The users collection is the authoritative Main Bot
    # registration source.
    profile = None
    if raw.lstrip("+").isdigit():
        try:
            numeric_id = int(raw)
        except (TypeError, ValueError):
            numeric_id = None
        if numeric_id is not None:
            profile = await db["users"].find_one({"user_id": numeric_id})
            if profile:
                # Search must include every Main Bot user, but searching alone
                # must not turn an ordinary user into a Seller List entry.
                return {**profile, "owner_id": int(numeric_id)}

    if username and not any(ch.isspace() for ch in username):
        user = await db["users"].find_one({"username": at_exact})
        if user:
            user_id = user.get("user_id")
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                user_id = None
            if user_id is not None:
                # Search-only result: keep the Main Bot profile intact without
                # creating a persistent seller record for an ordinary user.
                return {**user, "owner_id": int(user_id)}

    return None
