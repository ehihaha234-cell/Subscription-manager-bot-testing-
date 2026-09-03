import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4
from pymongo import ReturnDocument
from database.mongo import get_database

SETTINGS="seller_settings"; PLANS="seller_plans"; CHANNELS="seller_channels"; USERS="seller_users"
PAYMENTS="seller_payments"; SUBS="seller_subscriptions"; REFERRALS="seller_referrals"
BUSINESS_ACCOUNTS="seller_business_accounts"; BUSINESS_CONTACTS="seller_business_contacts"


def c(name): return get_database()[name]


async def initialize_seller_data_indexes():
    await c(SETTINGS).create_index("owner_id", unique=True)
    await c(PLANS).create_index([("owner_id",1),("plan_id",1)], unique=True)
    await c(CHANNELS).create_index([("owner_id",1),("chat_id",1)], unique=True)
    await c(USERS).create_index([("owner_id",1),("user_id",1)], unique=True)
    await c(PAYMENTS).create_index([("owner_id",1),("status",1),("created_at",-1)])
    await c(SUBS).create_index([("owner_id",1),("user_id",1)], unique=True)
    await c(SUBS).create_index([("owner_id",1),("active",1),("expiry_date",1)])
    await c(REFERRALS).create_index([("owner_id",1),("referred_user_id",1)], unique=True)
    await c(REFERRALS).create_index([("owner_id",1),("referrer_user_id",1),("rewarded",1)])
    await c(BUSINESS_ACCOUNTS).create_index([("owner_id",1),("account_user_id",1)], unique=True)
    await c(BUSINESS_ACCOUNTS).create_index([("owner_id",1),("active",1),("created_at",1)])
    await c(BUSINESS_CONTACTS).create_index([("owner_id",1),("account_user_id",1),("peer_user_id",1)], unique=True)


async def ensure_seller_defaults(owner_id:int, bot_name="Subscription Bot"):
    now=datetime.now(timezone.utc)
    defaults={
        "owner_id":owner_id,
        "bot_name":bot_name,
        "welcome_message":f"👋 Welcome to {bot_name}!",
        "support_username":"",
        "currency":"INR",
        "timezone":"Asia/Kolkata",
        "reminder_days":1,
        "upi_id":"",
        "upi_name":"",
        "upi_qr_file_id":"",
        "welcome_media_type":"",
        "welcome_media_file_id":"",
        "welcome_buttons":[],
        "referral_reward_days":7,
        "referral_unlock_enabled":False,
        "referral_unlock_required":3,
        "referral_unlock_duration_days":30,
        "referral_unlock_target_chat_id":None,
        "referral_unlock_target_title":"",
        "referral_unlock_count_mode":"subscription",
        "business_automation_enabled":False,
        "business_welcome_enabled":True,
        "business_welcome_once":True,
        "business_reply_delay_seconds":0,
        "business_welcome_message":f"👋 Welcome to {bot_name}!",
        "business_welcome_media_type":"",
        "business_welcome_media_file_id":"",
        "business_welcome_buttons":[],
        "business_auto_reply_enabled":True,
        "business_auto_reply_message":"",
        "business_auto_reply_media_type":"",
        "business_auto_reply_media_file_id":"",
        "business_auto_reply_buttons":[],
        "business_templates_enabled":True,
        "business_reply_templates":[],
        "created_at":now,
        "updated_at":now,
    }
    await c(SETTINGS).update_one(
        {"owner_id":owner_id},
        {"$setOnInsert":defaults},
        upsert=True,
    )
    for key,value in defaults.items():
        if key in {"owner_id","created_at"}:
            continue
        await c(SETTINGS).update_one(
            {"owner_id":owner_id,key:{"$exists":False}},
            {"$set":{key:value,"updated_at":now}},
        )
    return await get_seller_settings(owner_id)


async def get_seller_settings(owner_id:int): return await c(SETTINGS).find_one({"owner_id":owner_id}) or {}
async def set_seller_setting(owner_id:int,key:str,value):
    allowed={"bot_name","welcome_message","support_username","currency","timezone","reminder_days","upi_id","upi_name","upi_qr_file_id","welcome_media_type","welcome_media_file_id","welcome_buttons","referral_reward_days","referral_unlock_enabled","referral_unlock_required","referral_unlock_duration_days","referral_unlock_target_chat_id","referral_unlock_target_title","referral_unlock_count_mode","business_automation_enabled","business_welcome_enabled","business_welcome_once","business_reply_delay_seconds","business_welcome_message","business_welcome_media_type","business_welcome_media_file_id","business_welcome_buttons","business_auto_reply_enabled","business_auto_reply_message","business_auto_reply_media_type","business_auto_reply_media_file_id","business_auto_reply_buttons","business_templates_enabled","business_reply_templates","business_ignore_outgoing","business_anti_loop","business_flood_protection","business_working_hours_enabled","business_working_hours_start","business_working_hours_end","business_working_hours_timezone","business_action_button_mode"}
    if key not in allowed: raise ValueError("Unsupported setting")
    now=datetime.now(timezone.utc)
    await c(SETTINGS).update_one({"owner_id":owner_id},{"$set":{key:value,"updated_at":now},"$setOnInsert":{"owner_id":owner_id,"created_at":now}},upsert=True)


async def save_business_pending_auth(
    owner_id:int,
    *,
    attempt_id:str,
    step:str,
    phone:str,
    encrypted_session:str,
    phone_code_hash:str="",
    expires_at=None,
):
    """Persist the current MTProto login attempt so OTP and 2FA use the same auth key."""
    now=datetime.now(timezone.utc)
    if expires_at is None:
        expires_at=now+timedelta(minutes=10)
    payload={
        "attempt_id":str(attempt_id),
        "step":str(step),
        "phone":str(phone),
        "encrypted_session":str(encrypted_session),
        "phone_code_hash":str(phone_code_hash or ""),
        "created_at":now,
        "updated_at":now,
        "expires_at":expires_at,
    }
    await c(SETTINGS).update_one(
        {"owner_id":int(owner_id)},
        {
            "$set":{"business_pending_auth":payload,"updated_at":now},
            "$setOnInsert":{"owner_id":int(owner_id),"created_at":now},
        },
        upsert=True,
    )
    return payload


async def get_business_pending_auth(owner_id:int):
    doc=await c(SETTINGS).find_one(
        {"owner_id":int(owner_id)},
        {"business_pending_auth":1},
    )
    auth=(doc or {}).get("business_pending_auth")
    if not isinstance(auth,dict):
        return None
    expires_at=auth.get("expires_at")
    now=datetime.now(timezone.utc)
    if expires_at is not None:
        if getattr(expires_at,"tzinfo",None) is None:
            expires_at=expires_at.replace(tzinfo=timezone.utc)
        if expires_at<=now:
            await clear_business_pending_auth(owner_id,attempt_id=auth.get("attempt_id"))
            return None
    return auth


async def clear_business_pending_auth(owner_id:int, attempt_id:str|None=None):
    query={"owner_id":int(owner_id)}
    if attempt_id:
        query["business_pending_auth.attempt_id"]=str(attempt_id)
    result=await c(SETTINGS).update_one(
        query,
        {
            "$unset":{"business_pending_auth":""},
            "$set":{"updated_at":datetime.now(timezone.utc)},
        },
    )
    return result.modified_count>0


async def get_business_accounts(owner_id:int, active_only:bool=True):
    query={"owner_id":int(owner_id)}
    if active_only:
        query["active"]=True
    return await c(BUSINESS_ACCOUNTS).find(query).sort("created_at",1).to_list(length=100)


async def count_business_accounts(owner_id:int, active_only:bool=True):
    query={"owner_id":int(owner_id)}
    if active_only:
        query["active"]=True
    return await c(BUSINESS_ACCOUNTS).count_documents(query)


async def save_business_account(owner_id:int, account_user_id:int, *, phone:str="", username:str="", first_name:str=""):
    now=datetime.now(timezone.utc)
    await c(BUSINESS_ACCOUNTS).update_one(
        {"owner_id":int(owner_id),"account_user_id":int(account_user_id)},
        {
            "$set":{
                "phone":str(phone or ""),
                "username":str(username or ""),
                "first_name":str(first_name or ""),
                "active":True,
                "connection_status":"connected",
                "updated_at":now,
            },
            "$setOnInsert":{
                "owner_id":int(owner_id),
                "account_user_id":int(account_user_id),
                "created_at":now,
                "welcome_sent":0,
                "auto_replies_sent":0,
                "templates_used":0,
            },
        },
        upsert=True,
    )
    return await c(BUSINESS_ACCOUNTS).find_one({"owner_id":int(owner_id),"account_user_id":int(account_user_id)})


async def save_business_account_session(
    owner_id:int,
    account_user_id:int,
    *,
    encrypted_session:str,
    phone:str="",
    username:str="",
    first_name:str="",
):
    """Persist one authorized MTProto session for a seller account."""
    now=datetime.now(timezone.utc)
    await c(BUSINESS_ACCOUNTS).update_one(
        {"owner_id":int(owner_id),"account_user_id":int(account_user_id)},
        {
            "$set":{
                "phone":str(phone or ""),
                "username":str(username or ""),
                "first_name":str(first_name or ""),
                "encrypted_session":str(encrypted_session),
                "active":True,
                "connection_status":"connected",
                "last_connected_at":now,
                "updated_at":now,
            },
            "$setOnInsert":{
                "owner_id":int(owner_id),
                "account_user_id":int(account_user_id),
                "created_at":now,
                "welcome_sent":0,
                "auto_replies_sent":0,
                "templates_used":0,
            },
        },
        upsert=True,
    )
    return await c(BUSINESS_ACCOUNTS).find_one(
        {"owner_id":int(owner_id),"account_user_id":int(account_user_id)}
    )


async def get_business_account(owner_id:int, account_user_id:int):
    return await c(BUSINESS_ACCOUNTS).find_one({
        "owner_id":int(owner_id),
        "account_user_id":int(account_user_id),
    })


async def disconnect_business_account(owner_id:int, account_user_id:int):
    now=datetime.now(timezone.utc)
    result=await c(BUSINESS_ACCOUNTS).update_one(
        {"owner_id":int(owner_id),"account_user_id":int(account_user_id),"active":True},
        {
            "$set":{
                "active":False,
                "connection_status":"disconnected",
                "updated_at":now,
            },
            "$unset":{"encrypted_session":""},
        },
    )
    return result.matched_count>0


async def get_all_active_business_accounts():
    return await c(BUSINESS_ACCOUNTS).find({
        "active":True,
        "encrypted_session":{"$exists":True,"$ne":""},
    }).to_list(length=None)


async def get_business_contact(owner_id:int, account_user_id:int, peer_user_id:int):
    return await c(BUSINESS_CONTACTS).find_one({
        "owner_id":int(owner_id),
        "account_user_id":int(account_user_id),
        "peer_user_id":int(peer_user_id),
    })


async def claim_business_welcome(
    owner_id:int,
    account_user_id:int,
    peer_user_id:int,
    *,
    welcome_once:bool=True,
    force_new_conversation:bool=False,
    current_message_id:int=0,
):
    """Atomically claim a welcome for one private peer.

    ``force_new_conversation`` is used when Telegram history/deletion signals show
    that the previous chat was cleared.  The record is reset atomically before
    claiming, so a stale contact document can never suppress the next welcome.
    """
    now=datetime.now(timezone.utc)
    query={
        "owner_id":int(owner_id),
        "account_user_id":int(account_user_id),
        "peer_user_id":int(peer_user_id),
    }

    if force_new_conversation:
        await c(BUSINESS_CONTACTS).delete_one(query)

    if not welcome_once:
        await c(BUSINESS_CONTACTS).update_one(
            query,
            {
                "$set":{"last_message_at":now,"last_message_id":int(current_message_id or 0)},
                "$setOnInsert":{"first_message_at":now,"message_count":0},
                "$inc":{"message_count":1},
            },
            upsert=True,
        )
        return True

    try:
        result=await c(BUSINESS_CONTACTS).update_one(
            query,
            {
                "$setOnInsert":{
                    **query,
                    "first_message_at":now,
                    "last_message_at":now,
                    "last_message_id":int(current_message_id or 0),
                    "message_count":1,
                }
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            await increment_business_account_stat(owner_id,account_user_id,"conversations")
            return True
    except Exception:
        # Concurrent claims are expected; only the first insert may send welcome.
        pass

    await c(BUSINESS_CONTACTS).update_one(
        query,
        {"$set":{"last_message_at":now,"last_message_id":int(current_message_id or 0)},"$inc":{"message_count":1}},
    )
    return False


async def set_business_welcome_message_ids(
    owner_id:int, account_user_id:int, peer_user_id:int, message_ids:list[int]
):
    """Store sent welcome message ids so chat clearing can be detected reliably."""
    ids=[int(x) for x in (message_ids or []) if int(x or 0)>0]
    result=await c(BUSINESS_CONTACTS).update_one(
        {
            "owner_id":int(owner_id),
            "account_user_id":int(account_user_id),
            "peer_user_id":int(peer_user_id),
        },
        {"$set":{
            "welcome_message_ids":ids[-20:],
            "welcome_tracking_version":2,
            "welcome_sent_at":datetime.now(timezone.utc),
        }},
    )
    return result.matched_count>0


async def business_automation_stats(owner_id:int):
    """Return Official Telegram Business Automation statistics only.

    Normal MTProto account data is intentionally excluded from this page.
    Customer totals are calculated from the official Business recipient
    collection, while automation actions are read from official connection
    counters and cumulative broadcast totals.
    """
    owner_id = int(owner_id)
    official_connections = "seller_official_business_connections"
    official_recipients = "business_automation_business_recipients"
    broadcast_collection = "business_automation_broadcast"

    active_accounts = await c(official_connections).count_documents(
        {"owner_id": owner_id, "enabled": True}
    )
    total_accounts = await c(official_connections).count_documents(
        {"owner_id": owner_id}
    )
    total_customers = await c(official_recipients).count_documents(
        {"owner_id": owner_id}
    )
    active_customers = await c(official_recipients).count_documents(
        {"owner_id": owner_id, "active": True}
    )

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    today_start_utc = now_ist.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    active_today = await c(official_recipients).count_documents({
        "owner_id": owner_id,
        "active": True,
        "last_seen_at": {"$gte": today_start_utc},
    })

    fields = [
        "conversations", "welcome_sent", "auto_replies_sent", "templates_used",
        "plans_opened", "renew_opened", "profile_opened", "referral_opened",
    ]
    group = {"_id": None}
    for field in fields:
        group[field] = {"$sum": {"$ifNull": [f"${field}", 0]}}
    rows = await c(official_connections).aggregate([
        {"$match": {"owner_id": owner_id}},
        {"$group": group},
    ]).to_list(length=1)
    activity = rows[0] if rows else {}

    broadcast = await c(broadcast_collection).find_one(
        {"owner_id": owner_id},
        {
            "_id": 0,
            "broadcasts_sent": 1,
            "broadcast_recipients": 1,
            "broadcast_fully_delivered": 1,
            "broadcast_partially_delivered": 1,
            "broadcast_failed": 1,
            "last_report": 1,
            "last_sent_at": 1,
        },
    ) or {}
    last_report = broadcast.get("last_report") or {}

    return {
        "accounts": active_accounts,
        "accounts_total": total_accounts,
        "connected_users": active_customers,
        "customers_total": total_customers,
        "active_today": active_today,
        "conversations": max(
            total_customers,
            int(activity.get("conversations", 0) or 0),
        ),
        "welcome_sent": int(activity.get("welcome_sent", 0) or 0),
        "auto_replies_sent": int(activity.get("auto_replies_sent", 0) or 0),
        "templates_used": int(activity.get("templates_used", 0) or 0),
        "plans_opened": int(activity.get("plans_opened", 0) or 0),
        "renew_opened": int(activity.get("renew_opened", 0) or 0),
        "profile_opened": int(activity.get("profile_opened", 0) or 0),
        "referral_opened": int(activity.get("referral_opened", 0) or 0),
        "broadcasts_sent": int(broadcast.get("broadcasts_sent", 0) or 0),
        "broadcast_recipients": int(broadcast.get("broadcast_recipients", 0) or 0),
        "broadcast_fully_delivered": int(
            broadcast.get("broadcast_fully_delivered", 0) or 0
        ),
        "broadcast_partially_delivered": int(
            broadcast.get("broadcast_partially_delivered", 0) or 0
        ),
        "broadcast_failed": int(broadcast.get("broadcast_failed", 0) or 0),
        "last_broadcast_full": int(
            last_report.get("full", last_report.get("fully_delivered", last_report.get("sent", 0))) or 0
        ),
        "last_broadcast_partial": int(
            last_report.get("partial", last_report.get("partially_delivered", 0)) or 0
        ),
        "last_broadcast_failed": int(last_report.get("failed", 0) or 0),
        "last_broadcast_at": broadcast.get("last_sent_at"),
    }


async def increment_business_account_stat(owner_id:int, account_user_id:int, field:str, amount:int=1):
    allowed={
        "conversations","welcome_sent","auto_replies_sent","templates_used",
        "plans_opened","renew_opened","profile_opened","referral_opened",
    }
    if field not in allowed:
        raise ValueError("Unsupported business statistic")
    result=await c(BUSINESS_ACCOUNTS).update_one(
        {"owner_id":int(owner_id),"account_user_id":int(account_user_id),"active":True},
        {"$inc":{field:int(amount)},"$set":{"updated_at":datetime.now(timezone.utc)}},
    )
    return result.matched_count>0


async def create_plan(owner_id, name, duration_text, duration_minutes, price, stars_price=0):
    """Create a seller plan with optional Telegram Stars pricing.

    ``stars_price`` is optional so older callers remain compatible.
    """
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Plan name is required")

    minutes = int(duration_minutes)
    if minutes <= 0:
        raise ValueError("Plan duration must be greater than 0")

    fiat_price = float(price)
    if fiat_price < 0:
        raise ValueError("Plan price cannot be negative")

    stars = int(stars_price or 0)
    if stars < 0:
        raise ValueError("Stars price cannot be negative")

    now = datetime.now(timezone.utc)
    doc = {
        "owner_id": int(owner_id),
        "plan_id": uuid4().hex[:12],
        "name": clean_name,
        "duration_text": str(duration_text),
        "duration_minutes": minutes,
        "price": fiat_price,
        "stars_price": stars,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    await c(PLANS).insert_one(doc)
    return doc
async def get_plan(owner_id,plan_id): return await c(PLANS).find_one({"owner_id":owner_id,"plan_id":plan_id})
async def get_plans(owner_id,active_only=False):
    q={"owner_id":owner_id};
    if active_only:q["active"]=True
    return await c(PLANS).find(q).sort("price",1).to_list(length=100)
async def update_plan(owner_id,plan_id,**values):
    values["updated_at"]=datetime.now(timezone.utc); r=await c(PLANS).update_one({"owner_id":owner_id,"plan_id":plan_id},{"$set":values}); return r.matched_count>0
async def delete_plan(owner_id,plan_id): return (await c(PLANS).delete_one({"owner_id":owner_id,"plan_id":plan_id})).deleted_count>0


async def add_channel(owner_id,chat_id,title,chat_type):
    """Connect a subscription chat without implicitly activating new features.

    Existing connected chats keep their current settings. A genuinely new chat:
    - gets Subscription Guard disabled by default (admin must enable it);
    - gets Auto Invite enabled only when it is the first connected chat;
    - gets Auto Invite disabled for every subsequent connected chat.
    """
    owner_id = int(owner_id)
    chat_id = int(chat_id)
    now=datetime.now(timezone.utc)

    # Count only currently active connections. This decides the default for a
    # genuinely new connection; existing documents are never reset.
    existing_active = await c(CHANNELS).count_documents({
        "owner_id": owner_id,
        "active": True,
    })

    result = await c(CHANNELS).update_one(
        {"owner_id":owner_id,"chat_id":chat_id},
        {
            "$set":{"title":title,"chat_type":chat_type,"active":True,"updated_at":now},
            "$setOnInsert":{
                "owner_id":owner_id,
                "chat_id":chat_id,
                # Only the first connected subscription destination receives
                # automatic invite delivery by default.
                "auto_invite_enabled": existing_active == 0,
                "created_at":now,
            },
        },
        upsert=True,
    )

    # New connections must not silently become Subscription Guard targets.
    # We create an explicit disabled state so the legacy/default behaviour for
    # older connections remains untouched.
    if result.upserted_id is not None:
        await c("subscription_guard_chats").update_one(
            {"owner_id": owner_id, "chat_id": chat_id},
            {
                "$setOnInsert": {
                    "owner_id": owner_id,
                    "chat_id": chat_id,
                    "enabled": False,
                    "created_at": now,
                }
            },
            upsert=True,
        )

    return await c(CHANNELS).find_one({"owner_id":owner_id,"chat_id":chat_id})


async def get_channels(owner_id):
    return await c(CHANNELS).find({"owner_id":owner_id,"active":True}).to_list(length=100)


async def set_channel_auto_invite(owner_id:int, chat_id:int, enabled:bool):
    """Enable or disable automatic post-verification invite delivery for one chat."""
    result=await c(CHANNELS).update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id),"active":True},
        {"$set":{"auto_invite_enabled":bool(enabled),"updated_at":datetime.now(timezone.utc)}},
    )
    return result.matched_count>0


async def save_owner_access_invite_link(owner_id:int, chat_id:int, invite_link:str):
    """Store the reusable, no-expiry owner access link for one connected chat."""
    now=datetime.now(timezone.utc)
    result=await c(CHANNELS).update_one(
        {"owner_id":int(owner_id),"chat_id":int(chat_id),"active":True},
        {"$set":{
            "owner_access_invite_link":str(invite_link),
            "owner_access_link_updated_at":now,
            "updated_at":now,
        }},
    )
    return result.matched_count>0
async def remove_channel(owner_id,chat_id): return (await c(CHANNELS).update_one({"owner_id":owner_id,"chat_id":int(chat_id)},{"$set":{"active":False,"updated_at":datetime.now(timezone.utc)}})).matched_count>0


async def upsert_user(owner_id,user):
    """Create/update and return the user in one MongoDB round trip."""
    now=datetime.now(timezone.utc)
    username=user.username or ""
    return await c(USERS).find_one_and_update(
        {"owner_id":owner_id,"user_id":user.id},
        {
            "$set":{
                "first_name":user.first_name,
                "last_name":user.last_name,
                "username":username,
                "username_normalized":username.lower(),
                "language_code":user.language_code,
                "updated_at":now,
            },
            "$setOnInsert":{
                "owner_id":owner_id,
                "user_id":user.id,
                "joined_at":now,
                "banned":False,
                "ban_reason":"",
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
async def get_user(owner_id,user_id): return await c(USERS).find_one({"owner_id":owner_id,"user_id":user_id})
async def count_users(owner_id): return await c(USERS).count_documents({"owner_id":owner_id})


async def get_user_by_username(owner_id:int, username:str):
    normalized=username.strip().lstrip("@").lower()
    if not normalized:
        return None
    return await c(USERS).find_one(
        {"owner_id":owner_id,"username_normalized":normalized}
    )


async def set_user_ban(owner_id:int, user_id:int, banned:bool, reason:str=""):
    now=datetime.now(timezone.utc)
    result=await c(USERS).update_one(
        {"owner_id":owner_id,"user_id":int(user_id)},
        {"$set":{
            "banned":bool(banned),
            "ban_reason":reason.strip() if banned else "",
            "updated_at":now,
        }},
    )
    return result.matched_count>0


async def remove_subscription(owner_id:int, user_id:int):
    now=datetime.now(timezone.utc)
    result=await c(SUBS).update_one(
        {"owner_id":owner_id,"user_id":int(user_id)},
        {"$set":{
            "active":False,
            "removed_by_admin":True,
            "updated_at":now,
        }},
    )
    return result.matched_count>0


async def create_payment(owner_id,user_id,plan,screenshot_file_id):
    now=datetime.now(timezone.utc); doc={"owner_id":owner_id,"payment_id":uuid4().hex[:16],"user_id":user_id,"plan_id":plan["plan_id"],"plan":plan["name"],"amount":plan["price"],"duration_text":plan["duration_text"],"duration_minutes":plan["duration_minutes"],"screenshot_file_id":screenshot_file_id,"status":"pending","created_at":now,"updated_at":now}
    await c(PAYMENTS).insert_one(doc); return doc

async def create_automatic_payment(owner_id,user_id,plan,gateway,transaction_id,gateway_payment_id=""):
    now=datetime.now(timezone.utc)
    doc={
        "owner_id":int(owner_id),"payment_id":str(transaction_id),"user_id":int(user_id),
        "plan_id":plan["plan_id"],"plan":plan["name"],"amount":float(plan["price"]),
        "duration_text":plan["duration_text"],"duration_minutes":int(plan["duration_minutes"]),
        "payment_method":gateway,"gateway_payment_id":str(gateway_payment_id or ""),
        "status":"approved","admin_id":0,"processed_at":now,"created_at":now,"updated_at":now,
    }
    result=await c(PAYMENTS).update_one(
        {"owner_id":int(owner_id),"payment_id":str(transaction_id)},
        {"$setOnInsert":doc},upsert=True,
    )
    payment=await c(PAYMENTS).find_one({"owner_id":int(owner_id),"payment_id":str(transaction_id)})
    if payment is not None:
        payment["_created_now"] = result.upserted_id is not None
    return payment
async def get_payment(owner_id,payment_id): return await c(PAYMENTS).find_one({"owner_id":owner_id,"payment_id":payment_id})
async def pending_payments(owner_id): return await c(PAYMENTS).find({"owner_id":owner_id,"status":"pending"}).sort("created_at",-1).to_list(length=50)
async def payment_history(owner_id): return await c(PAYMENTS).find({"owner_id":owner_id,"status":{"$in":["approved","rejected"]}}).sort("updated_at",-1).to_list(length=50)
async def set_payment_status(owner_id,payment_id,status,admin_id):
    now=datetime.now(timezone.utc)
    r=await c(PAYMENTS).update_one(
        {
            "owner_id":owner_id,
            "payment_id":payment_id,
            "status":{"$in":["pending","processing"]},
        },
        {
            "$set":{
                "status":status,
                "admin_id":admin_id,
                "processed_at":now,
                "updated_at":now,
            }
        },
    )
    return r.modified_count>0


async def claim_payment_for_processing(owner_id,payment_id,admin_id):
    now=datetime.now(timezone.utc)
    r=await c(PAYMENTS).update_one(
        {
            "owner_id":owner_id,
            "payment_id":payment_id,
            "status":"pending",
        },
        {
            "$set":{
                "status":"processing",
                "processing_admin_id":admin_id,
                "processing_started_at":now,
                "updated_at":now,
            }
        },
    )
    return r.modified_count>0


async def finalize_processed_payment(owner_id,payment_id,status,admin_id):
    now=datetime.now(timezone.utc)
    r=await c(PAYMENTS).update_one(
        {
            "owner_id":owner_id,
            "payment_id":payment_id,
            "status":"processing",
        },
        {
            "$set":{
                "status":status,
                "admin_id":admin_id,
                "processed_at":now,
                "updated_at":now,
            },
            "$unset":{
                "processing_admin_id":"",
                "processing_started_at":"",
                "processing_error":"",
            },
        },
    )
    return r.modified_count>0


async def release_processing_payment(owner_id,payment_id,error_message=""):
    now=datetime.now(timezone.utc)
    r=await c(PAYMENTS).update_one(
        {
            "owner_id":owner_id,
            "payment_id":payment_id,
            "status":"processing",
        },
        {
            "$set":{
                "status":"pending",
                "processing_error":str(error_message)[:500],
                "updated_at":now,
            },
            "$unset":{
                "processing_admin_id":"",
                "processing_started_at":"",
            },
        },
    )
    return r.modified_count>0


async def get_subscription(owner_id,user_id): return await c(SUBS).find_one({"owner_id":owner_id,"user_id":user_id})
async def activate_subscription(
    owner_id,
    user_id,
    plan_name,
    duration_minutes,
    amount=None,
    duration_text=None,
):
    now=datetime.now(timezone.utc)
    current=await get_subscription(owner_id,user_id)

    current_expiry=(current or {}).get("expiry_date")
    if current_expiry and current_expiry.tzinfo is None:
        current_expiry=current_expiry.replace(tzinfo=timezone.utc)
    elif current_expiry:
        current_expiry=current_expiry.astimezone(timezone.utc)

    # Renewal always starts from the remaining expiry when it is still active.
    # This prevents any already-paid remaining validity from being lost.
    if current and current.get("active") and current_expiry and current_expiry>now:
        base=current_expiry
    else:
        base=now

    added_minutes=int(duration_minutes)
    expiry=base+timedelta(minutes=added_minutes)

    previous_total_minutes=int((current or {}).get("total_duration_minutes") or 0)
    previous_total_paid=float((current or {}).get("total_paid") or 0)
    payment_amount=float(amount or 0)

    values={
        "plan":plan_name,
        "active":True,
        "expiry_date":expiry,
        "last_renewed_at":now,
        "last_added_minutes":added_minutes,
        "total_duration_minutes":previous_total_minutes+added_minutes,
        "total_paid":previous_total_paid+payment_amount,
        "removed_by_admin":False,
        "updated_at":now,
    }

    if amount is not None:
        values["amount"]=amount
        values["last_payment_amount"]=amount
    if duration_text is not None:
        values["duration_text"]=duration_text
        values["last_duration_text"]=duration_text

    if not current or not current.get("active") or not current_expiry or current_expiry<=now:
        values["start_date"]=now

    await c(SUBS).update_one(
        {"owner_id":owner_id,"user_id":user_id},
        {
            "$set":values,
            "$setOnInsert":{
                "owner_id":owner_id,
                "user_id":user_id,
                "created_at":now,
            },
        },
        upsert=True,
    )
    return expiry

async def fulfill_subscription_payment(
    owner_id,
    user_id,
    fulfillment_key,
    plan_name,
    duration_minutes,
    amount=None,
    duration_text=None,
):
    """Idempotently activate/extend one seller subscription payment.

    The fulfillment key is stored in the same MongoDB update that changes the
    expiry date. Webhook retries, recovery jobs, or duplicate admin callbacks
    therefore cannot add the same purchased duration twice.
    """
    now = datetime.now(timezone.utc)
    key = str(fulfillment_key or "").strip()
    if not key:
        raise ValueError("fulfillment_key is required")

    added_minutes = max(0, int(duration_minutes or 0))
    payment_amount = float(amount or 0)

    already_applied = {
        "$in": [key, {"$ifNull": ["$fulfillment_keys", []]}]
    }
    active_before = {
        "$and": [
            {"$eq": [{"$ifNull": ["$active", False]}, True]},
            {"$gt": [{"$ifNull": ["$expiry_date", now]}, now]},
        ]
    }
    base_expiry = {"$cond": [active_before, "$expiry_date", now]}
    new_expiry = {
        "$dateAdd": {
            "startDate": base_expiry,
            "unit": "minute",
            "amount": added_minutes,
        }
    }

    set_fields = {
        "owner_id": int(owner_id),
        "user_id": int(user_id),
        "plan": {"$cond": [already_applied, {"$ifNull": ["$plan", plan_name]}, plan_name]},
        "active": {"$cond": [already_applied, {"$ifNull": ["$active", True]}, True]},
        "expiry_date": {"$cond": [already_applied, "$expiry_date", new_expiry]},
        "last_renewed_at": {"$cond": [already_applied, "$last_renewed_at", now]},
        "last_added_minutes": {"$cond": [already_applied, "$last_added_minutes", added_minutes]},
        "total_duration_minutes": {
            "$cond": [
                already_applied,
                {"$ifNull": ["$total_duration_minutes", 0]},
                {"$add": [{"$ifNull": ["$total_duration_minutes", 0]}, added_minutes]},
            ]
        },
        "total_paid": {
            "$cond": [
                already_applied,
                {"$ifNull": ["$total_paid", 0]},
                {"$add": [{"$ifNull": ["$total_paid", 0]}, payment_amount]},
            ]
        },
        "amount": {"$cond": [already_applied, "$amount", payment_amount]},
        "last_payment_amount": {"$cond": [already_applied, "$last_payment_amount", payment_amount]},
        "duration_text": {"$cond": [already_applied, "$duration_text", duration_text or ""]},
        "last_duration_text": {"$cond": [already_applied, "$last_duration_text", duration_text or ""]},
        "removed_by_admin": {"$cond": [already_applied, {"$ifNull": ["$removed_by_admin", False]}, False]},
        "start_date": {
            "$cond": [
                already_applied,
                {"$ifNull": ["$start_date", now]},
                {"$cond": [active_before, {"$ifNull": ["$start_date", now]}, now]},
            ]
        },
        "created_at": {"$ifNull": ["$created_at", now]},
        "updated_at": {"$cond": [already_applied, {"$ifNull": ["$updated_at", now]}, now]},
        "fulfillment_keys": {
            "$cond": [
                already_applied,
                {"$ifNull": ["$fulfillment_keys", []]},
                {"$concatArrays": [{"$ifNull": ["$fulfillment_keys", []]}, [key]]},
            ]
        },
        "last_fulfillment_key": {"$cond": [already_applied, "$last_fulfillment_key", key]},
    }

    result = await c(SUBS).find_one_and_update(
        {"owner_id": int(owner_id), "user_id": int(user_id)},
        [{"$set": set_fields}],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {
        "expiry_date": (result or {}).get("expiry_date"),
        "subscription": result or {},
        "fulfillment_key": key,
    }


async def active_subscriptions(owner_id, limit=5000):
    now=datetime.now(timezone.utc)
    cursor = c(SUBS).find({
        "owner_id":owner_id,
        "active":True,
        "expiry_date":{"$gt":now},
    })
    if limit is None:
        return [doc async for doc in cursor]
    return await cursor.to_list(length=limit)


async def expired_subscriptions(owner_id):
    now=datetime.now(timezone.utc); return await c(SUBS).find({"owner_id":owner_id,"active":True,"expiry_date":{"$lte":now}}).to_list(length=500)
async def mark_expired(owner_id,user_id): await c(SUBS).update_one({"owner_id":owner_id,"user_id":user_id},{"$set":{"active":False,"updated_at":datetime.now(timezone.utc)}})


async def register_referral(owner_id:int, referrer_user_id:int, referred_user_id:int):
    if not referrer_user_id or not referred_user_id or referrer_user_id == referred_user_id:
        return {"created":False,"reason":"invalid"}

    existing = await c(REFERRALS).find_one(
        {"owner_id":owner_id,"referred_user_id":referred_user_id}
    )
    if existing:
        return {"created":False,"reason":"already_registered","record":existing}

    now = datetime.now(timezone.utc)
    doc = {
        "owner_id":owner_id,
        "referrer_user_id":int(referrer_user_id),
        "referred_user_id":int(referred_user_id),
        "rewarded":False,
        "created_at":now,
        "updated_at":now,
    }
    await c(REFERRALS).insert_one(doc)
    return {"created":True,"record":doc}


async def count_successful_referrals(owner_id:int, referrer_user_id:int):
    return await c(REFERRALS).count_documents(
        {
            "owner_id":owner_id,
            "referrer_user_id":int(referrer_user_id),
            "rewarded":True,
        }
    )


async def count_all_referrals(owner_id:int, referrer_user_id:int):
    return await c(REFERRALS).count_documents(
        {
            "owner_id":owner_id,
            "referrer_user_id":int(referrer_user_id),
        }
    )


async def mark_referral_rewarded(
    owner_id:int,
    referred_user_id:int,
    payment_id:str|None=None,
):
    """Atomically claim a referral reward without marking it completed yet."""
    now = datetime.now(timezone.utc)
    return await c(REFERRALS).find_one_and_update(
        {
            "owner_id":owner_id,
            "referred_user_id":int(referred_user_id),
            "rewarded":False,
            "reward_status":{"$nin":["processing","rewarded"]},
        },
        {
            "$set":{
                "reward_status":"processing",
                "reward_payment_id":str(payment_id) if payment_id else None,
                "reward_claimed_at":now,
                "updated_at":now,
            },
            "$inc":{"reward_attempts":1},
        },
        return_document=ReturnDocument.AFTER,
    )


async def finalize_referral_reward(
    owner_id:int,
    referred_user_id:int,
    payment_id:str|None=None,
):
    now = datetime.now(timezone.utc)
    query = {
        "owner_id":owner_id,
        "referred_user_id":int(referred_user_id),
        "rewarded":False,
        "reward_status":"processing",
    }
    if payment_id:
        query["reward_payment_id"] = str(payment_id)

    result = await c(REFERRALS).update_one(
        query,
        {
            "$set":{
                "rewarded":True,
                "reward_status":"rewarded",
                "rewarded_at":now,
                "updated_at":now,
            },
            "$unset":{"reward_error":""},
        },
    )
    return result.modified_count == 1


async def release_referral_reward(
    owner_id:int,
    referred_user_id:int,
    error:str,
    payment_id:str|None=None,
):
    now = datetime.now(timezone.utc)
    query = {
        "owner_id":owner_id,
        "referred_user_id":int(referred_user_id),
        "rewarded":False,
        "reward_status":"processing",
    }
    if payment_id:
        query["reward_payment_id"] = str(payment_id)

    result = await c(REFERRALS).update_one(
        query,
        {
            "$set":{
                "reward_status":"failed",
                "reward_error":str(error)[:500],
                "reward_failed_at":now,
                "updated_at":now,
            }
        },
    )
    return result.modified_count == 1


async def stats(owner_id):
    """Return a complete seller/clone-bot statistics snapshot.

    Backward-compatible keys (``users``, ``active``, ``plans``, ``channels``,
    ``pending`` and ``revenue``) are preserved for older dashboard callers.
    """
    owner_id = int(owner_id)
    now = datetime.now(timezone.utc)

    settings = await c(SETTINGS).find_one({"owner_id": owner_id}) or {}
    timezone_name = settings.get("timezone") or "Asia/Kolkata"
    try:
        local_tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        local_tz = ZoneInfo("Asia/Kolkata")

    local_now = now.astimezone(local_tz)
    local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = local_day_start.astimezone(timezone.utc)

    active_subscription_query = {
        "owner_id": owner_id,
        "active": True,
        "expiry_date": {"$gt": now},
    }
    active_today_query = {
        "owner_id": owner_id,
        "updated_at": {"$gte": day_start_utc},
    }

    total_revenue_pipeline = [
        {"$match": {"owner_id": owner_id, "status": "approved"}},
        {"$group": {"_id": None, "total": {"$sum": {"$convert": {
            "input": "$amount", "to": "double", "onError": 0, "onNull": 0,
        }}}}},
    ]
    today_revenue_pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "status": "approved",
            "$expr": {"$gte": [
                {"$ifNull": ["$processed_at", {"$ifNull": ["$updated_at", "$created_at"]}]},
                day_start_utc,
            ]},
        }},
        {"$group": {"_id": None, "total": {"$sum": {"$convert": {
            "input": "$amount", "to": "double", "onError": 0, "onNull": 0,
        }}}}},
    ]

    (
        total_users,
        active_users_today,
        active_subscribers,
        plans,
        channels,
        pending,
        total_revenue_rows,
        today_revenue_rows,
    ) = await asyncio.gather(
        c(USERS).count_documents({"owner_id": owner_id}),
        c(USERS).count_documents(active_today_query),
        c(SUBS).count_documents(active_subscription_query),
        c(PLANS).count_documents({"owner_id": owner_id}),
        c(CHANNELS).count_documents({"owner_id": owner_id, "active": True}),
        c(PAYMENTS).count_documents({"owner_id": owner_id, "status": "pending"}),
        c(PAYMENTS).aggregate(total_revenue_pipeline).to_list(length=1),
        c(PAYMENTS).aggregate(today_revenue_pipeline).to_list(length=1),
    )

    total_revenue = float(total_revenue_rows[0].get("total", 0) or 0) if total_revenue_rows else 0.0
    today_revenue = float(today_revenue_rows[0].get("total", 0) or 0) if today_revenue_rows else 0.0

    return {
        "users": int(total_users),
        "total_users": int(total_users),
        "active_users_today": int(active_users_today),
        "active_subscribers": int(active_subscribers),
        "active": int(active_subscribers),
        "plans": int(plans),
        "channels": int(channels),
        "pending": int(pending),
        "today_revenue": today_revenue,
        "total_revenue": total_revenue,
        "revenue": total_revenue,
    }

async def reset_business_welcome(owner_id:int, account_user_id:int, peer_user_id:int):
    """Forget the first-contact claim so the next incoming message receives welcome again."""
    result = await c(BUSINESS_CONTACTS).delete_one({
        "owner_id": int(owner_id),
        "account_user_id": int(account_user_id),
        "peer_user_id": int(peer_user_id),
    })
    return bool(result.deleted_count)


async def reset_business_welcome_for_peer(owner_id:int, peer_user_id:int):
    """Reset all first-contact keys for one Official Business customer.

    Older releases used either the seller owner id or the Telegram Business
    account id as ``account_user_id``. Deleting all matching variants prevents a
    stale legacy row from suppressing the next welcome after chat history clear.
    """
    result = await c(BUSINESS_CONTACTS).delete_many({
        "owner_id": int(owner_id),
        "peer_user_id": int(peer_user_id),
    })
    return int(result.deleted_count or 0)
