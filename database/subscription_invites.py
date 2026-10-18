"""Invite-link records for the normal subscription lifecycle (not Subscription Guard)."""
from datetime import datetime, timezone
from database.mongo import get_database

COLLECTION = "subscription_invite_lifecycle"


def _collection():
    return get_database()[COLLECTION]


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def save_invite(owner_id, user_id, chat_id, invite_link, expiry_date=None, group_id=None):
    """Store a generated invite link; legacy 4-argument calls remain supported."""
    if not invite_link:
        return False
    now = _utc()
    doc = {
        "owner_id": int(owner_id),
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "invite_link": str(invite_link),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    if expiry_date is not None:
        doc["expiry_date"] = _utc(expiry_date)
    if group_id is not None:
        doc["group_id"] = str(group_id)
    await _collection().update_one(
        {"owner_id": int(owner_id), "invite_link": str(invite_link)},
        {"$set": {k: v for k, v in doc.items() if k not in ("created_at",)},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return True


async def record_subscription_invite(owner_id, user_id, chat_id, invite_link, expiry_date, group_id=None):
    return await save_invite(owner_id, user_id, chat_id, invite_link, expiry_date, group_id)


async def active_invites_for_user(owner_id, user_id):
    return await _collection().find({
        "owner_id": int(owner_id), "user_id": int(user_id), "status": "active"
    }).to_list(length=1000)


async def active_invites_for_user_chat(owner_id, user_id, chat_id=None):
    query = {"owner_id": int(owner_id), "user_id": int(user_id), "status": "active"}
    if chat_id is not None:
        query["chat_id"] = int(chat_id)
    return await _collection().find(query).to_list(length=1000)


async def update_active_invite_expiry(owner_id, user_id, group_id, expiry_date):
    """Move existing active links for this user's Plan Group to the new expiry.

    Used by User Management Extend so previously issued links remain tied to
    the updated subscription expiry instead of being revoked at the old date.
    This belongs to the normal subscription lifecycle, not Subscription Guard.
    """
    if expiry_date is None:
        return 0
    result = await _collection().update_many(
        {
            "owner_id": int(owner_id),
            "user_id": int(user_id),
            "group_id": str(group_id),
            "status": "active",
        },
        {"$set": {"expiry_date": _utc(expiry_date), "updated_at": _utc()}},
    )
    return int(getattr(result, "modified_count", 0))


async def mark_invite_revoked(owner_id, invite_link):
    now = _utc()
    result = await _collection().update_one(
        {"owner_id": int(owner_id), "invite_link": str(invite_link), "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now, "updated_at": now}},
    )
    return bool(getattr(result, "modified_count", 0))


async def deactivate_invite(owner_id, invite_link):
    return await mark_invite_revoked(owner_id, invite_link)


async def revoke_subscription_invites(bot, owner_id, user_id=None, group_id=None, due_only=False, chat_ids=None):
    """Revoke normal-subscription invite links for the selected user/chats.

    First use the expected owner scope. For legacy Extend records whose owner_id
    was written differently, fall back only to the same Telegram user and the
    explicitly selected target chats. Telegram itself validates whether this bot
    owns each invite link; failures are left retryable.
    """
    now = _utc()
    owner_id = int(owner_id)
    normalized_chat_ids = None
    if chat_ids is not None:
        normalized_chat_ids = []
        for value in chat_ids:
            try:
                normalized_chat_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        normalized_chat_ids = list(dict.fromkeys(normalized_chat_ids))
        if not normalized_chat_ids:
            return 0

    base = {"status": "active"}
    if user_id is not None:
        base["user_id"] = int(user_id)
    if normalized_chat_ids is not None:
        base["chat_id"] = {"$in": normalized_chat_ids}
    elif group_id is not None:
        base["group_id"] = str(group_id)
    if due_only:
        base["expiry_date"] = {"$lte": now}

    primary = dict(base)
    primary["owner_id"] = owner_id
    rows = await _collection().find(primary).to_list(length=1000)

    # Compatibility for older Extend records: some versions stored the seller
    # ID in a different owner field. Keep the fallback strictly scoped to the
    # same user and selected target chats, and de-duplicate by invite URL.
    if not rows and user_id is not None and normalized_chat_ids:
        fallback = dict(base)
        fallback.pop("owner_id", None)
        rows = await _collection().find(fallback).to_list(length=1000)

    revoked = 0
    seen = set()
    for row in rows:
        link = str(row.get("invite_link") or "")
        if not link or link in seen:
            continue
        seen.add(link)
        try:
            await bot.revoke_chat_invite_link(
                chat_id=int(row["chat_id"]), invite_link=link
            )
        except Exception as exc:
            message = str(exc).lower()
            already_invalid = any(term in message for term in (
                "invite link is already revoked", "invite link is invalid",
                "invite link has expired", "invite link not found",
            ))
            if not already_invalid:
                continue
        # Update by the record's actual owner ID, not the lookup owner argument.
        try:
            await _collection().update_one(
                {"owner_id": row.get("owner_id"), "invite_link": link, "status": "active"},
                {"$set": {"status": "revoked", "revoked_at": now, "updated_at": now}},
            )
        except Exception:
            pass
        revoked += 1
    return revoked

