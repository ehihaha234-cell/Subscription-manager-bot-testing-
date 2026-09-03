from __future__ import annotations

from datetime import datetime, timedelta, timezone
from database.mongo import get_database

COLLECTION = "seller_group_manager"

DEFAULT_PROTECTION = {
    "anti_spam": {
        "telegram_links": {"action": "off", "delete": False, "username_antispam": False, "bots_antispam": False},
        "forwarding": {"channels": False, "groups": False, "users": False, "bots": False, "action": "off", "delete": False},
        "quote": {"enabled": False, "action": "off", "delete": False},
        "total_links": {"action": "off", "delete": False},
    },
    "anti_flood": {
        "messages": 5,
        "seconds": 3,
        "action": "off",
        "delete": True,
        "warn_duration_seconds": 1800,
        "mute_duration_seconds": 1800,
        "ban_duration_seconds": 1800,
    },
    "banned_words": {
        "words": [],
        "action": "off",
        "delete": True,
    },
    "warns": {
        "action": "mute",
        "max_warns": 3,
        "mute_minutes": 30,
    },
    "warned_users": {},
}

def _c():
    return get_database()[COLLECTION]

def _now():
    return datetime.now(timezone.utc)

def _merge(default, value):
    if not isinstance(default, dict):
        return value if value is not None else default
    out = {}
    value = value or {}
    for k, v in default.items():
        out[k] = _merge(v, value.get(k)) if isinstance(v, dict) else value.get(k, v)
    for k, v in value.items():
        if k not in out:
            out[k] = v
    return out

async def get_protection(owner_id:int, chat_id:int):
    doc = await _c().find_one({"owner_id":int(owner_id),"chat_id":int(chat_id)}) or {}
    return _merge(DEFAULT_PROTECTION, doc.get("protection") or {})

async def set_protection(owner_id:int, chat_id:int, path:str, value):
    await _c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$set":{f"protection.{path}":value,"updated_at":_now()}},
        upsert=True,
    )
    return await get_protection(owner_id,chat_id)

async def add_banned_word(owner_id:int, chat_id:int, word:str):
    word = " ".join(str(word or "").strip().casefold().split())
    if not word:
        return await get_protection(owner_id,chat_id)
    await _c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$addToSet":{"protection.banned_words.words":word},"$set":{"updated_at":_now()}},
        upsert=True,
    )
    return await get_protection(owner_id,chat_id)

async def remove_banned_word(owner_id:int, chat_id:int, word:str):
    word = " ".join(str(word or "").strip().casefold().split())
    await _c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$pull":{"protection.banned_words.words":word},"$set":{"updated_at":_now()}},
        upsert=True,
    )
    return await get_protection(owner_id,chat_id)

async def get_warns(owner_id:int, chat_id:int, user_id:int) -> int:
    p = await get_protection(owner_id,chat_id)
    return int((p.get("warned_users") or {}).get(str(int(user_id)),0) or 0)

async def set_warns(owner_id:int, chat_id:int, user_id:int, count:int):
    await set_protection(owner_id,chat_id,f"warned_users.{int(user_id)}",max(0,int(count)))

async def increment_warn(owner_id:int, chat_id:int, user_id:int, expires_seconds=None):
    key=f"protection.warned_users.{int(user_id)}"
    await _c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$inc":{key:1},"$set":{"updated_at":_now()}},
        upsert=True,
    )
    return await get_warns(owner_id,chat_id,user_id)

async def clear_warn(owner_id:int, chat_id:int, user_id:int):
    await _c().update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id)},
        {"$unset":{f"protection.warned_users.{int(user_id)}":""},"$set":{"updated_at":_now()}},
        upsert=True,
    )

async def warned_list(owner_id:int, chat_id:int):
    p=await get_protection(owner_id,chat_id)
    return p.get("warned_users") or {}
