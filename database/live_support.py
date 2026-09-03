from datetime import datetime, timedelta, timezone
import uuid

from pymongo.errors import DuplicateKeyError

from database.mongo import get_database

SETTINGS = "clone_live_support_settings"
TOPICS = "clone_live_support_topics"
MESSAGE_LINKS = "clone_live_support_message_links"
BLOCKS = "clone_live_support_blocks"
TEMPLATES = "clone_live_support_templates"
AUTO_REPLIES = "clone_live_support_auto_replies"


def c(name: str):
    return get_database()[name]


async def initialize_live_support_indexes():
    await c(SETTINGS).create_index("owner_id", unique=True)
    await c(TOPICS).create_index([("owner_id", 1), ("user_id", 1)], unique=True)
    # Only ready topic mappings have a thread id. A normal unique compound
    # index also indexes documents where message_thread_id is missing, which
    # can block two different users from creating topics concurrently.
    old_name = "owner_id_1_support_group_id_1_message_thread_id_1"
    try:
        await c(TOPICS).drop_index(old_name)
    except Exception:
        pass
    await c(TOPICS).create_index(
        [("owner_id", 1), ("support_group_id", 1), ("message_thread_id", 1)],
        unique=True,
        name="uniq_ready_support_thread",
        partialFilterExpression={"message_thread_id": {"$type": "number"}},
    )
    await c(MESSAGE_LINKS).create_index(
        [("owner_id", 1), ("admin_chat_id", 1), ("admin_message_id", 1)],
        unique=True,
    )
    await c(MESSAGE_LINKS).create_index("created_at", expireAfterSeconds=60 * 60 * 24 * 180)
    await c(BLOCKS).create_index([("owner_id", 1), ("user_id", 1)], unique=True)
    await c(TEMPLATES).create_index([("owner_id", 1), ("command", 1)], unique=True)
    await c(AUTO_REPLIES).create_index([("owner_id", 1), ("keyword", 1)], unique=True)
    await initialize_live_support_delivery_indexes()


async def get_live_support_settings(owner_id: int):
    now = datetime.now(timezone.utc)
    defaults = {
        "owner_id": int(owner_id),
        "enabled": False,
        "mode": "topic",
        "support_group_id": None,
        "support_group_title": "",
        "created_at": now,
        "updated_at": now,
    }
    await c(SETTINGS).update_one(
        {"owner_id": int(owner_id)},
        {"$setOnInsert": defaults},
        upsert=True,
    )
    return await c(SETTINGS).find_one({"owner_id": int(owner_id)}) or defaults


async def update_live_support_settings(owner_id: int, **values):
    allowed = {
        "enabled",
        "mode",
        "support_group_id",
        "support_group_title",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if "mode" in clean and clean["mode"] not in {"private", "topic"}:
        raise ValueError("Support mode must be private or topic")
    clean["updated_at"] = datetime.now(timezone.utc)
    await c(SETTINGS).update_one(
        {"owner_id": int(owner_id)},
        {
            "$set": clean,
            "$setOnInsert": {
                "owner_id": int(owner_id),
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )
    return await get_live_support_settings(owner_id)




async def claim_support_topic_creation(
    owner_id: int,
    user_id: int,
    support_group_id: int,
    lease_seconds: int = 30,
):
    """Acquire a cross-process lease for creating one user's support topic.

    In-memory asyncio locks only protect one Python process. Render may run
    multiple clone runtimes/processes, so creation must also be serialized in
    MongoDB. Returns ``(token, document)``; token is None when another worker
    owns the lease or a ready topic already exists.
    """
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    owner_id = int(owner_id)
    user_id = int(user_id)
    support_group_id = int(support_group_id)

    current = await c(TOPICS).find_one({"owner_id": owner_id, "user_id": user_id})
    if current:
        ready = (
            int(current.get("support_group_id") or 0) == support_group_id
            and current.get("message_thread_id")
            and current.get("status") != "failed"
        )
        if ready:
            return None, current
        lease_until = current.get("claim_expires_at")
        if (
            current.get("status") == "creating"
            and lease_until
            and lease_until > now
        ):
            return None, current

    query = {"owner_id": owner_id, "user_id": user_id}
    if current:
        query["_id"] = current["_id"]
    update = {
        "$set": {
            "support_group_id": support_group_id,
            "status": "creating",
            "claim_token": token,
            "claim_expires_at": now + timedelta(seconds=max(10, int(lease_seconds))),
            "updated_at": now,
        },
        "$unset": {
            "message_thread_id": "",
            "topic_name": "",
            "header_message_id": "",
            "header_sent": "",
        },
        "$setOnInsert": {
            "owner_id": owner_id,
            "user_id": user_id,
            "created_at": now,
        },
    }
    try:
        await c(TOPICS).update_one(query, update, upsert=not bool(current))
    except DuplicateKeyError:
        return None, await get_support_topic(owner_id, user_id)
    claimed = await c(TOPICS).find_one(
        {"owner_id": owner_id, "user_id": user_id, "claim_token": token}
    )
    return (token if claimed else None), (claimed or await get_support_topic(owner_id, user_id))


async def complete_support_topic_creation(
    owner_id: int,
    user_id: int,
    claim_token: str,
    support_group_id: int,
    message_thread_id: int,
    topic_name: str,
    header_message_id: int | None = None,
):
    """Publish a Telegram topic mapping as ready.

    The mapping must become usable even when the optional user-details header
    cannot be sent. Customer-message delivery must never depend on the header.
    """
    now = datetime.now(timezone.utc)
    values = {
        "support_group_id": int(support_group_id),
        "message_thread_id": int(message_thread_id),
        "topic_name": str(topic_name),
        "status": "ready",
        "updated_at": now,
    }
    unset = {"claim_token": "", "claim_expires_at": ""}
    if header_message_id:
        values["header_message_id"] = int(header_message_id)
        values["header_sent"] = True
    else:
        values["header_sent"] = False
        unset["header_message_id"] = ""

    await c(TOPICS).update_one(
        {
            "owner_id": int(owner_id),
            "user_id": int(user_id),
            "claim_token": str(claim_token),
        },
        {"$set": values, "$unset": unset},
    )
    return await get_support_topic(owner_id, user_id)


async def fail_support_topic_creation(owner_id: int, user_id: int, claim_token: str):
    await c(TOPICS).update_one(
        {
            "owner_id": int(owner_id),
            "user_id": int(user_id),
            "claim_token": str(claim_token),
        },
        {
            "$set": {"status": "failed", "updated_at": datetime.now(timezone.utc)},
            "$unset": {"claim_token": "", "claim_expires_at": ""},
        },
    )


async def claim_support_topic_header(owner_id: int, user_id: int) -> bool:
    """Claim sending the user-details header exactly once across processes."""
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    result = await c(TOPICS).update_one(
        {
            "owner_id": int(owner_id),
            "user_id": int(user_id),
            "header_sent": {"$ne": True},
            "$or": [
                {"header_claim_expires_at": {"$exists": False}},
                {"header_claim_expires_at": {"$lt": now}},
            ],
        },
        {
            "$set": {
                "header_claim_token": token,
                "header_claim_expires_at": now + timedelta(seconds=30),
                "updated_at": now,
            }
        },
    )
    return bool(result.modified_count)


async def release_support_topic_header_claim(owner_id: int, user_id: int):
    """Release a failed header-send lease immediately.

    Header delivery is best effort and must never block or fail the customer's
    actual support message.
    """
    await c(TOPICS).update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$unset": {
                "header_claim_token": "",
                "header_claim_expires_at": "",
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )


async def mark_support_topic_header(
    owner_id: int, user_id: int, header_message_id: int
):
    await c(TOPICS).update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$set": {
                "header_sent": True,
                "header_message_id": int(header_message_id),
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {"header_claim_token": "", "header_claim_expires_at": ""},
        },
    )


async def save_support_topic(
    owner_id: int,
    user_id: int,
    support_group_id: int,
    message_thread_id: int,
    topic_name: str,
):
    now = datetime.now(timezone.utc)
    await c(TOPICS).update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$set": {
                "support_group_id": int(support_group_id),
                "message_thread_id": int(message_thread_id),
                "topic_name": topic_name,
                "updated_at": now,
            },
            "$setOnInsert": {
                "owner_id": int(owner_id),
                "user_id": int(user_id),
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await get_support_topic(owner_id, user_id)


async def get_support_topic(owner_id: int, user_id: int):
    return await c(TOPICS).find_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)}
    )


async def get_topic_by_thread(
    owner_id: int,
    support_group_id: int,
    message_thread_id: int,
):
    return await c(TOPICS).find_one(
        {
            "owner_id": int(owner_id),
            "support_group_id": int(support_group_id),
            "message_thread_id": int(message_thread_id),
        }
    )


async def reset_support_topic_mapping(owner_id: int, user_id: int, reason: str = "stale_topic"):
    """Keep the permanent user record but clear an unusable Telegram thread.

    Blocking/unblocking the bot or leaving and returning never removes the
    mapping. This reset is only used when Telegram confirms that the forum
    topic itself no longer exists or belongs to another support group.
    """
    now = datetime.now(timezone.utc)
    await c(TOPICS).update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$set": {
                "status": "stale",
                "stale_reason": str(reason or "stale_topic")[:300],
                "updated_at": now,
            },
            "$unset": {
                "message_thread_id": "",
                "header_message_id": "",
                "header_sent": "",
                "header_claim_token": "",
                "header_claim_expires_at": "",
                "claim_token": "",
                "claim_expires_at": "",
            },
        },
    )


async def delete_support_topic(owner_id: int, user_id: int):
    """Backward-compatible alias that preserves the permanent user record."""
    await reset_support_topic_mapping(owner_id, user_id, "legacy_reset")


async def save_private_message_link(
    owner_id: int,
    admin_chat_id: int,
    admin_message_id: int,
    user_id: int,
):
    now = datetime.now(timezone.utc)
    await c(MESSAGE_LINKS).update_one(
        {
            "owner_id": int(owner_id),
            "admin_chat_id": int(admin_chat_id),
            "admin_message_id": int(admin_message_id),
        },
        {
            "$set": {
                "user_id": int(user_id),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def get_private_message_link(
    owner_id: int,
    admin_chat_id: int,
    admin_message_id: int,
):
    return await c(MESSAGE_LINKS).find_one(
        {
            "owner_id": int(owner_id),
            "admin_chat_id": int(admin_chat_id),
            "admin_message_id": int(admin_message_id),
        }
    )


async def set_support_block(owner_id: int, user_id: int, blocked: bool):
    now = datetime.now(timezone.utc)
    await c(BLOCKS).update_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        {
            "$set": {"blocked": bool(blocked), "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def is_support_blocked(owner_id: int, user_id: int) -> bool:
    doc = await c(BLOCKS).find_one(
        {"owner_id": int(owner_id), "user_id": int(user_id)}
    )
    return bool(doc and doc.get("blocked"))


async def count_support_blocks(owner_id: int) -> int:
    return await c(BLOCKS).count_documents(
        {"owner_id": int(owner_id), "blocked": True}
    )


async def initialize_live_support_template_indexes():
    await c(TEMPLATES).create_index(
        [("owner_id", 1), ("command", 1)], unique=True
    )


async def list_support_templates(owner_id: int):
    return await c(TEMPLATES).find(
        {"owner_id": int(owner_id)}
    ).sort("command", 1).to_list(length=None)


async def get_support_template(owner_id: int, command: str):
    command = str(command or "").strip().lower().lstrip("/")
    return await c(TEMPLATES).find_one(
        {"owner_id": int(owner_id), "command": command}
    )


async def save_support_template(owner_id: int, command: str, **values):
    command = str(command or "").strip().lower().lstrip("/")
    if (
        not command
        or len(command) > 20
        or not command.replace("_", "").isalnum()
    ):
        raise ValueError(
            "Command me sirf letters, numbers aur underscore use karo (max 20)"
        )

    allowed = {
        "text",
        "media_type",
        "media_file_id",
        "buttons",
        "buttons_input",
        "enabled",
        "auto_delete_minutes",
        "auto_delete_seconds",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if "auto_delete_minutes" in clean:
        minutes = int(clean["auto_delete_minutes"] or 0)
        if minutes < 0 or minutes > 10080:
            raise ValueError("Auto remove 0 se 10080 minutes ke beech rakho")
        clean["auto_delete_minutes"] = minutes
    if "auto_delete_seconds" in clean:
        seconds = int(clean["auto_delete_seconds"] or 0)
        if seconds < 0 or seconds > 604800:
            raise ValueError("Auto remove 0 seconds se 7 days ke beech rakho")
        clean["auto_delete_seconds"] = seconds

    now = datetime.now(timezone.utc)
    clean["updated_at"] = now

    # Do not write the same path in $set and $setOnInsert. MongoDB treats that
    # as a conflicting update, which was why Text/Media/Buttons were not saving.
    insert_defaults = {
        "owner_id": int(owner_id),
        "command": command,
        "text": "",
        "media_type": "",
        "media_file_id": "",
        "buttons": [],
        "buttons_input": "",
        "enabled": True,
        "auto_delete_minutes": 0,
        "auto_delete_seconds": 0,
        "created_at": now,
    }
    for key in clean:
        insert_defaults.pop(key, None)

    await c(TEMPLATES).update_one(
        {"owner_id": int(owner_id), "command": command},
        {"$set": clean, "$setOnInsert": insert_defaults},
        upsert=True,
    )
    return await get_support_template(owner_id, command)


async def delete_support_template(owner_id: int, command: str):
    command = str(command or "").strip().lower().lstrip("/")
    return await c(TEMPLATES).delete_one(
        {"owner_id": int(owner_id), "command": command}
    )


async def list_support_auto_replies(owner_id: int):
    return await c(AUTO_REPLIES).find(
        {"owner_id": int(owner_id)}
    ).sort("keyword", 1).to_list(length=None)


async def get_support_auto_reply(owner_id: int, keyword: str):
    keyword = " ".join(str(keyword or "").strip().lower().split())
    return await c(AUTO_REPLIES).find_one(
        {"owner_id": int(owner_id), "keyword": keyword}
    )


async def save_support_auto_reply(owner_id: int, keyword: str, **values):
    keyword = " ".join(str(keyword or "").strip().lower().split())
    if (
        not keyword
        or len(keyword) > 20
        or not keyword.replace(" ", "").replace("_", "").isalnum()
    ):
        raise ValueError("Use only letters, numbers, spaces or underscore (max 20 characters)")

    allowed = {"text", "media_type", "media_file_id", "buttons", "buttons_input", "enabled"}
    clean = {key: value for key, value in values.items() if key in allowed}
    now = datetime.now(timezone.utc)
    clean["updated_at"] = now
    defaults = {
        "owner_id": int(owner_id),
        "keyword": keyword,
        "text": "",
        "media_type": "",
        "media_file_id": "",
        "buttons": [],
        "buttons_input": "",
        "enabled": True,
        "created_at": now,
    }
    for key in clean:
        defaults.pop(key, None)
    await c(AUTO_REPLIES).update_one(
        {"owner_id": int(owner_id), "keyword": keyword},
        {"$set": clean, "$setOnInsert": defaults},
        upsert=True,
    )
    return await get_support_auto_reply(owner_id, keyword)


async def delete_support_auto_reply(owner_id: int, keyword: str):
    keyword = " ".join(str(keyword or "").strip().lower().split())
    return await c(AUTO_REPLIES).delete_one(
        {"owner_id": int(owner_id), "keyword": keyword}
    )


async def match_support_auto_reply(owner_id: int, message_text: str):
    normalized = " ".join(str(message_text or "").strip().lower().split())
    if not normalized:
        return None
    items = await list_support_auto_replies(owner_id)
    # Prefer the longest matching keyword so a specific phrase wins over a short word.
    items.sort(key=lambda item: len(item.get("keyword", "")), reverse=True)
    padded = f" {normalized} "
    for item in items:
        if item.get("enabled", True) is False:
            continue
        keyword = " ".join(str(item.get("keyword") or "").split())
        if keyword and f" {keyword} " in padded:
            return item
    return None

# Delivery receipts make forwarding idempotent across retries and restarts.
DELIVERIES = "clone_live_support_deliveries"


async def initialize_live_support_delivery_indexes():
    await c(DELIVERIES).create_index(
        [("owner_id", 1), ("direction", 1), ("source_chat_id", 1), ("source_message_id", 1)],
        unique=True,
    )
    await c(DELIVERIES).create_index("updated_at", expireAfterSeconds=60 * 60 * 24 * 180)


async def claim_support_delivery(
    owner_id: int,
    direction: str,
    source_chat_id: int,
    source_message_id: int,
    *,
    stale_seconds: int = 300,
):
    """Claim one support message exactly once, recovering stale attempts."""
    from datetime import timedelta
    from pymongo import ReturnDocument

    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=max(60, int(stale_seconds)))
    key = {
        "owner_id": int(owner_id),
        "direction": str(direction),
        "source_chat_id": int(source_chat_id),
        "source_message_id": int(source_message_id),
    }

    existing = await c(DELIVERIES).find_one(key)
    if existing and existing.get("status") == "completed":
        return None

    query = {
        **key,
        "$or": [
            {"status": {"$in": ["pending", "failed"]}},
            {"status": "processing", "claimed_at": {"$lt": stale_before}},
            {"status": {"$exists": False}},
        ],
    }
    update = {
        "$set": {
            "status": "processing",
            "claimed_at": now,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now},
        "$inc": {"attempts": 1},
    }

    # Two workers can both observe a missing receipt. The unique index allows
    # only one insert; the loser must return None instead of crashing support.
    try:
        return await c(DELIVERIES).find_one_and_update(
            query,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError

        if not isinstance(exc, DuplicateKeyError):
            raise
        return None


async def complete_support_delivery(receipt_id, **details):
    now = datetime.now(timezone.utc)
    clean = {k: v for k, v in details.items() if v is not None}
    clean.update({"status": "completed", "completed_at": now, "updated_at": now})
    await c(DELIVERIES).update_one(
        {"_id": receipt_id, "status": "processing"},
        {"$set": clean, "$unset": {"claimed_at": "", "last_error": ""}},
    )


async def fail_support_delivery(receipt_id, error: str):
    await c(DELIVERIES).update_one(
        {"_id": receipt_id, "status": "processing"},
        {
            "$set": {
                "status": "failed",
                "last_error": str(error)[:500],
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {"claimed_at": ""},
        },
    )
