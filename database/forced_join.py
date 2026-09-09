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

async def upsert_required(owner_id, access_chat_id, chat_id, title, chat_type, invite_link=""):
    """Save a Forced Join target for one specific access group/channel.

    ``access_chat_id`` is the Group Manager selected group/channel whose
    subscribers must satisfy this Forced Join configuration.  This scope keeps
    the same target independently configurable for every connected access chat.
    """
    key={
        "owner_id":int(owner_id),
        "access_chat_id":int(access_chat_id),
        "chat_id":int(chat_id),
    }
    await c().update_one(
        key,
        {"$set":{
            **key,
            "title":title or "Group/Channel",
            "chat_type":chat_type,
            "invite_link":invite_link or "",
            "updated_at":now(),
        },"$setOnInsert":{"created_at":now(),"enabled":False}},
        upsert=True,
    )
    return await c().find_one(key)

async def list_required(owner_id, access_chat_id=None):
    query={"owner_id":int(owner_id)}
    if access_chat_id is not None:
        query["access_chat_id"]=int(access_chat_id)
    else:
        # Legacy/global records are intentionally not returned as a shared
        # configuration.  New runtime/UI always uses an explicit scope.
        query["access_chat_id"]={"$exists":True,"$ne":0}
    return await c().find(query).sort("title",1).to_list(length=500)

async def get_required(owner_id, chat_id, access_chat_id=None):
    query={"owner_id":int(owner_id),"chat_id":int(chat_id)}
    if access_chat_id is not None:
        query["access_chat_id"]=int(access_chat_id)
    else:
        query["access_chat_id"]={"$exists":True,"$ne":0}
    return await c().find_one(query)

async def toggle_required(owner_id, chat_id, access_chat_id=None):
    doc=await get_required(owner_id,chat_id,access_chat_id)
    if not doc:
        return None
    enabled=not bool(doc.get("enabled",False))
    await c().update_one({"_id":doc["_id"]},{"$set":{"enabled":enabled,"updated_at":now()}})
    return await get_required(owner_id,chat_id,access_chat_id)

async def remove_required(owner_id, chat_id, access_chat_id=None):
    query={"owner_id":int(owner_id),"chat_id":int(chat_id)}
    if access_chat_id is not None:
        query["access_chat_id"]=int(access_chat_id)
    await c().delete_many(query)

async def update_invite(owner_id, chat_id, invite_link, access_chat_id=None):
    query={"owner_id":int(owner_id),"chat_id":int(chat_id)}
    if access_chat_id is not None:
        query["access_chat_id"]=int(access_chat_id)
    await c().update_many(
        query,
        {"$set":{"invite_link":invite_link or "","updated_at":now()}},
    )

async def ensure_required_scope(owner_id, access_chat_id):
    """Populate a newly opened Group Manager scope from known detected targets.

    This only copies the target catalog (title/type/invite); enabled is always
    initialized to False.  It never copies another group's enable state.
    """
    owner_id=int(owner_id)
    access_chat_id=int(access_chat_id)
    if not access_chat_id:
        return
    existing = await c().find({
        "owner_id": owner_id,
        "access_chat_id": access_chat_id,
    }).to_list(length=500)
    existing_ids={int(x.get("chat_id",0) or 0) for x in existing}
    known = await c().find({
        "owner_id": owner_id,
        "access_chat_id":{"$nin":[0, access_chat_id]},
    }).to_list(length=2000)
    seen=set()
    for item in known:
        chat_id=int(item.get("chat_id",0) or 0)
        if not chat_id or chat_id in existing_ids or chat_id in seen:
            continue
        seen.add(chat_id)
        await c().update_one(
            {"owner_id":owner_id,"access_chat_id":access_chat_id,"chat_id":chat_id},
            {"$setOnInsert":{
                "owner_id":owner_id,
                "access_chat_id":access_chat_id,
                "chat_id":chat_id,
                "title":item.get("title") or "Group/Channel",
                "chat_type":item.get("chat_type") or "group",
                "invite_link":item.get("invite_link") or "",
                "enabled":False,
                "created_at":now(),
            }},
            upsert=True,
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



# Per-access-chat Forced Join approval message settings.  Each connected
# group/channel gets its own message configuration instead of sharing one
# owner-wide editor state.
CHAT_MESSAGE_COLLECTION = "seller_forced_join_chat_messages"

def chat_message_c():
    return get_database()[CHAT_MESSAGE_COLLECTION]

async def get_forced_join_editor_for_chat(owner_id, access_chat_id):
    doc = await chat_message_c().find_one({
        "owner_id": int(owner_id),
        "access_chat_id": int(access_chat_id),
    })
    if doc is not None:
        return doc.get("message") or {}
    # Backward compatibility: existing global configuration is used only as
    # the initial template. Once saved for a chat, that chat is independent.
    return await get_forced_join_editor(owner_id)

async def set_forced_join_editor_for_chat(owner_id, access_chat_id, message):
    key = {"owner_id": int(owner_id), "access_chat_id": int(access_chat_id)}
    await chat_message_c().update_one(
        key,
        {"$set": {
            **key,
            "message": message or {},
            "updated_at": now(),
        }, "$setOnInsert": {"created_at": now(), "approval_enabled": True}},
        upsert=True,
    )
    return message or {}

async def get_forced_join_editor_enabled_for_chat(owner_id, access_chat_id):
    doc = await chat_message_c().find_one({
        "owner_id": int(owner_id),
        "access_chat_id": int(access_chat_id),
    })
    if doc is not None and "approval_enabled" in doc:
        return bool(doc.get("approval_enabled"))
    return await get_forced_join_editor_enabled(owner_id)

async def set_forced_join_editor_enabled_for_chat(owner_id, access_chat_id, enabled):
    key = {"owner_id": int(owner_id), "access_chat_id": int(access_chat_id)}
    await chat_message_c().update_one(
        key,
        {"$set": {
            **key,
            "approval_enabled": bool(enabled),
            "updated_at": now(),
        }, "$setOnInsert": {"created_at": now(), "message": {}}},
        upsert=True,
    )
    return bool(enabled)

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


async def get_forced_join_enabled(owner_id, access_chat_id=None):
    """Return Forced Join master switch for one Group Manager selection."""
    if access_chat_id is None:
        doc = await settings_c().find_one({"owner_id":int(owner_id)})
        return bool((doc or {}).get("enabled", False))
    doc = await settings_c().find_one({
        "owner_id":int(owner_id),
        "access_chat_id":int(access_chat_id),
    })
    # New Group Manager selections start disabled until explicitly enabled.
    return bool((doc or {}).get("enabled", False))

async def set_forced_join_enabled(owner_id, enabled, access_chat_id=None):
    if access_chat_id is None:
        await settings_c().update_one(
            {"owner_id":int(owner_id)},
            {"$set":{"owner_id":int(owner_id),"enabled":bool(enabled),"updated_at":now()},
            "$setOnInsert":{"created_at":now()}},
            upsert=True,
        )
    else:
        await settings_c().update_one(
            {"owner_id":int(owner_id),"access_chat_id":int(access_chat_id)},
            {"$set":{
                "owner_id":int(owner_id),
                "access_chat_id":int(access_chat_id),
                "enabled":bool(enabled),
                "updated_at":now(),
            },"$setOnInsert":{"created_at":now()}},
            upsert=True,
        )
    return bool(enabled)

async def get_forced_join_editor_enabled(owner_id):
    """Whether the legacy/global post-approval custom message is enabled."""
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
