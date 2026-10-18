"""Invite-link lifecycle for normal clone subscriptions (independent of Subscription Guard)."""
from datetime import datetime, timezone
from database.mongo import get_database

COLLECTION = "subscription_invite_lifecycle"

def _c():
    return get_database()[COLLECTION]

async def record_subscription_invite(owner_id, user_id, chat_id, invite_link, expiry_date, group_id=None):
    if not invite_link or expiry_date is None:
        return False
    expiry = expiry_date
    if getattr(expiry, "tzinfo", None) is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    await _c().update_one(
        {"owner_id": int(owner_id), "invite_link": str(invite_link)},
        {"$set": {"owner_id": int(owner_id), "user_id": int(user_id), "chat_id": int(chat_id),
                   "invite_link": str(invite_link), "expiry_date": expiry,
                   "group_id": str(group_id) if group_id is not None else None,
                   "status": "active", "updated_at": now},
         "$setOnInsert": {"created_at": now}}, upsert=True)
    return True

async def revoke_subscription_invites(bot, owner_id, user_id=None, group_id=None, due_only=False):
    now = datetime.now(timezone.utc)
    query = {"owner_id": int(owner_id), "status": "active"}
    if user_id is not None: query["user_id"] = int(user_id)
    if group_id is not None: query["group_id"] = str(group_id)
    if due_only: query["expiry_date"] = {"$lte": now}
    rows = await _c().find(query).to_list(length=1000)
    revoked = 0
    for row in rows:
        try:
            await bot.revoke_chat_invite_link(chat_id=int(row["chat_id"]), invite_link=row["invite_link"])
        except Exception as exc:
            # Telegram may report an already-revoked/invalid link. Only mark it
            # terminal for known invalid/revoked responses; otherwise retry later.
            message = str(exc).lower()
            if not any(x in message for x in ("invite link is already revoked", "invite link is invalid", "invite link has expired")):
                continue
        await _c().update_one({"_id": row["_id"], "status": "active"}, {"$set": {"status": "revoked", "revoked_at": now, "updated_at": now}})
        revoked += 1
    return revoked
