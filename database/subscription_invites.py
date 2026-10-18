"""Invite-link lifecycle for normal subscriptions (independent of Subscription Guard)."""
from datetime import datetime, timezone
from database.mongo import get_database

COLLECTION = "subscription_invite_lifecycle"

def _c():
    return get_database()[COLLECTION]

async def save_invite(owner_id: int, user_id: int, chat_id: int, invite_link: str, subscription_scope: str = "standard"):
    now = datetime.now(timezone.utc)
    await _c().update_one(
        {"owner_id": int(owner_id), "invite_link": str(invite_link)},
        {"$set": {"owner_id": int(owner_id), "user_id": int(user_id), "chat_id": int(chat_id),
                   "invite_link": str(invite_link), "scope": str(subscription_scope),
                   "status": "active", "updated_at": now},
         "$setOnInsert": {"created_at": now}}, upsert=True)

async def active_invites_for_user(owner_id: int, user_id: int, scope: str | None = None):
    query = {"owner_id": int(owner_id), "user_id": int(user_id), "status": {"$ne": "revoked"}}
    if scope:
        query["scope"] = str(scope)
    return await _c().find(query).to_list(length=1000)

async def active_invites_for_user_chat(owner_id: int, user_id: int, chat_id: int):
    return await _c().find({"owner_id": int(owner_id), "user_id": int(user_id), "chat_id": int(chat_id), "status": {"$ne": "revoked"}}).to_list(length=500)

async def mark_invite_revoked(owner_id: int, invite_link: str):
    await _c().update_one({"owner_id": int(owner_id), "invite_link": str(invite_link)},
                          {"$set": {"status": "revoked", "revoked_at": datetime.now(timezone.utc)}})

async def pending_invites_for_user(owner_id: int, user_id: int):
    return await active_invites_for_user(owner_id, user_id)
