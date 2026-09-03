from copy import deepcopy
from datetime import datetime, timezone
import uuid
from database.mongo import get_database
from database.deleting_messages import DEFAULT_SETTINGS, deep_merge

COLLECTION='seller_group_manager'

def c(): return get_database()[COLLECTION]
def now(): return datetime.now(timezone.utc)

async def ensure_group(owner_id:int, chat_id:int, title:str='Group'):
    key={'owner_id':int(owner_id),'chat_id':int(chat_id)}
    await c().update_one(
        key,
        {
            '$setOnInsert': {
                **key,
                'welcome': {'enabled': False, 'text': '', 'media': [], 'buttons': [], 'delete_last_welcome': False, 'last_message_id': None},
                'auto_replies': [],
                'templates': [],
                'moderation': deepcopy(DEFAULT_SETTINGS),
                'created_at': now(),
            },
            '$set': {'title': title, 'updated_at': now()},
        },
        upsert=True,
    )
    return await c().find_one(key)

async def get_group(owner_id:int, chat_id:int, title:str='Group'):
    return await ensure_group(owner_id,chat_id,title)

async def update_welcome(owner_id,chat_id,**values):
    await ensure_group(owner_id,chat_id)
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$set':{**{f'welcome.{k}':v for k,v in values.items()},'updated_at':now()}})
    return await get_group(owner_id,chat_id)

async def get_moderation(owner_id,chat_id):
    doc=await get_group(owner_id,chat_id)
    return deep_merge(DEFAULT_SETTINGS,doc.get('moderation') or {})

async def set_moderation_value(owner_id,chat_id,path,value):
    await ensure_group(owner_id,chat_id)
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$set':{f'moderation.{path}':value,'updated_at':now()}})
    return await get_moderation(owner_id,chat_id)

async def set_moderation_values(owner_id, chat_id, values):
    await ensure_group(owner_id, chat_id)
    update = {f"moderation.{path}": value for path, value in (values or {}).items()}
    update["updated_at"] = now()
    await c().update_one(
        {"owner_id": int(owner_id), "chat_id": int(chat_id)},
        {"$set": update},
        upsert=True,
    )
    return await get_moderation(owner_id, chat_id)

async def reset_moderation(owner_id,chat_id):
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$set':{'moderation':deepcopy(DEFAULT_SETTINGS),'updated_at':now()}},upsert=True)

async def list_auto_replies(owner_id,chat_id):
    return (await get_group(owner_id,chat_id)).get('auto_replies') or []
async def save_auto_reply(owner_id,chat_id,item):
    doc=await get_group(owner_id,chat_id); items=doc.get('auto_replies') or []
    rid=item.get('id') or uuid.uuid4().hex[:10]; item={**item,'id':rid}
    items=[x for x in items if x.get('id')!=rid]+[item]
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$set':{'auto_replies':items,'updated_at':now()}}); return item
async def delete_auto_reply(owner_id,chat_id,rid):
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$pull':{'auto_replies':{'id':rid}},'$set':{'updated_at':now()}})

async def list_templates(owner_id,chat_id): return (await get_group(owner_id,chat_id)).get('templates') or []
async def save_template(owner_id,chat_id,item):
    doc=await get_group(owner_id,chat_id); items=doc.get('templates') or []
    tid=item.get('id') or uuid.uuid4().hex[:10]; item={**item,'id':tid}
    items=[x for x in items if x.get('id')!=tid]+[item]
    await c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)},{'$set':{'templates':items,'updated_at':now()}}); return item

async def get_auto_reply(owner_id,chat_id,rid):
    return next((x for x in await list_auto_replies(owner_id,chat_id) if x.get("id")==rid),None)
async def get_template(owner_id,chat_id,tid):
    return next((x for x in await list_templates(owner_id,chat_id) if x.get("id")==tid),None)


FORCED_JOIN_COLLECTION = 'seller_forced_join'

def forced_join_c():
    return get_database()[FORCED_JOIN_COLLECTION]

async def list_forced_join_chats(owner_id:int):
    cur=forced_join_c().find({'owner_id':int(owner_id)}).sort('title',1)
    return await cur.to_list(length=200)

async def add_forced_join_chat(owner_id:int, chat_id:int, title:str='Group/Channel', chat_type:str='supergroup'):
    doc={'owner_id':int(owner_id),'chat_id':int(chat_id)}
    await forced_join_c().update_one(doc, {'$set':{**doc,'title':title,'chat_type':chat_type,'updated_at':now()},'$setOnInsert':{'enabled':True,'created_at':now()}}, upsert=True)
    return await forced_join_c().find_one(doc)

async def set_forced_join_enabled(owner_id:int, chat_id:int, enabled:bool):
    await forced_join_c().update_one({'owner_id':int(owner_id),'chat_id':int(chat_id)}, {'$set':{'enabled':bool(enabled),'updated_at':now()}}, upsert=True)

async def get_forced_join_settings(owner_id:int, access_chat_id:int):
    doc=await forced_join_c().find_one({'owner_id':int(owner_id),'access_chat_id':int(access_chat_id)})
    return doc or {'enabled':False,'required_chat_ids':[]}

async def set_forced_join_settings(owner_id:int, access_chat_id:int, **values):
    key={'owner_id':int(owner_id),'access_chat_id':int(access_chat_id)}
    await forced_join_c().update_one(key, {'$set':{**values,'updated_at':now()},'$setOnInsert':{'created_at':now()}}, upsert=True)
    return await forced_join_c().find_one(key)
