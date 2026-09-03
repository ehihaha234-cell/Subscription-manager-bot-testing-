from datetime import datetime, timezone
from database.mongo import get_database

COLLECTION = "seller_forced_join"
PENDING_COLLECTION = "seller_forced_join_pending"

def c():
    return get_database()[COLLECTION]

def pending_c():
    return get_database()[PENDING_COLLECTION]

def now():
    return datetime.now(timezone.utc)

async def upsert_required(owner_id, chat_id, title, chat_type, invite_link=""):
    key={"owner_id":int(owner_id),"chat_id":int(chat_id)}
    await c().update_one(
        key,
        {"$set":{
            "owner_id":int(owner_id),"chat_id":int(chat_id),
            "title":title or "Group/Channel","chat_type":chat_type,
            "invite_link":invite_link or "","enabled":True,"updated_at":now()
        },"$setOnInsert":{"created_at":now()}},
        upsert=True,
    )
    return await c().find_one(key)

async def list_required(owner_id):
    return await c().find({"owner_id":int(owner_id)}).sort("title",1).to_list(length=200)

async def get_required(owner_id, chat_id):
    return await c().find_one({"owner_id":int(owner_id),"chat_id":int(chat_id)})

async def toggle_required(owner_id, chat_id):
    doc=await get_required(owner_id,chat_id)
    if not doc: return None
    enabled=not bool(doc.get("enabled",True))
    await c().update_one({"_id":doc["_id"]},{"$set":{"enabled":enabled,"updated_at":now()}})
    return await get_required(owner_id,chat_id)

async def remove_required(owner_id, chat_id):
    await c().delete_one({"owner_id":int(owner_id),"chat_id":int(chat_id)})

async def update_invite(owner_id, chat_id, invite_link):
    await c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$set":{"invite_link":invite_link or "","updated_at":now()}},
    )


async def save_pending_request(owner_id, user_id, access_chat_id, user_chat_id=None):
    key={
        "owner_id":int(owner_id),
        "user_id":int(user_id),
        "access_chat_id":int(access_chat_id),
    }
    chat_id=int(user_chat_id or user_id)
    await pending_c().update_one(
        key,
        {"$set":{**key, "user_chat_id":chat_id, "updated_at":now()},"$setOnInsert":{"created_at":now()}},
        upsert=True,
    )

async def list_pending_requests(owner_id, user_id):
    return await pending_c().find({
        "owner_id":int(owner_id),
        "user_id":int(user_id),
    }).to_list(length=50)

async def remove_pending_request(owner_id, user_id, access_chat_id):
    await pending_c().delete_one({
        "owner_id":int(owner_id),
        "user_id":int(user_id),
        "access_chat_id":int(access_chat_id),
    })


SETTINGS_COLLECTION = "seller_forced_join_settings"

def settings_c():
    return get_database()[SETTINGS_COLLECTION]

async def get_forced_join_editor(owner_id):
    doc=await settings_c().find_one({"owner_id":int(owner_id)})
    return (doc or {}).get("message") or {}

async def set_forced_join_editor(owner_id, message):
    await settings_c().update_one(
        {"owner_id":int(owner_id)},
        {"$set":{"owner_id":int(owner_id),"message":message,"updated_at":now()},
         "$setOnInsert":{"created_at":now()}},
        upsert=True,
    )
    return message


async def get_forced_join_enabled(owner_id):
    doc = await settings_c().find_one({"owner_id": int(owner_id)})
    return bool((doc or {}).get("enabled", True))

async def set_forced_join_enabled(owner_id, enabled):
    await settings_c().update_one(
        {"owner_id": int(owner_id)},
        {"$set": {"owner_id": int(owner_id), "enabled": bool(enabled), "updated_at": now()},
         "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )
    return bool(enabled)


async def get_forced_join_editor_enabled(owner_id):
    """Whether the post-approval custom message is enabled."""
    doc=await settings_c().find_one({"owner_id":int(owner_id)})
    return bool((doc or {}).get("approval_enabled", True))


async def set_forced_join_editor_enabled(owner_id, enabled):
    await settings_c().update_one(
        {"owner_id":int(owner_id)},
        {"$set":{
            "owner_id":int(owner_id),
            "approval_enabled":bool(enabled),
            "updated_at":now(),
        },
        "$setOnInsert":{"created_at":now()}},
        upsert=True,
    )
    return bool(enabled)
