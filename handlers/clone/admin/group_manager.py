import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from handlers.common.editor_engine import editor_header, editor_menu_keyboard, editor_text_prompt, editor_media_prompt
from handlers.common.feature_navigation import register_feature_origin
from handlers.clone.group_manager_buttons import group_buttons_header, parse_group_buttons, build_group_keyboard
from database.seller_data import get_channels
from database.seller_bots import get_bot_by_data_owner_id
from database.group_manager import get_group, update_welcome, list_auto_replies, save_auto_reply, list_templates, save_template, get_moderation, set_moderation_value, reset_moderation, get_auto_reply, get_template, set_moderation_values
from database.group_manager_protection import get_protection, set_protection, add_banned_word, remove_banned_word, warned_list

logger=logging.getLogger(__name__)
def kb(rows): return InlineKeyboardMarkup(rows)
def _duration_text(seconds):
    seconds=max(0,int(seconds or 0))
    if seconds<=0: return "No duration"
    parts=[]
    for size,name in ((86400,"day"),(3600,"hour"),(60,"minute"),(1,"second")):
        n,seconds=divmod(seconds,size)
        if n: parts.append(f"{n} {name}{'' if n==1 else 's'}")
    return " ".join(parts) or "0 seconds"

def _parse_duration(text):
    text=" ".join(str(text or "").strip().casefold().split())
    if text in {"0","remove","none","off"}: return 0
    units={"second":1,"seconds":1,"sec":1,"secs":1,"minute":60,"minutes":60,"min":60,"mins":60,"hour":3600,"hours":3600,"hr":3600,"hrs":3600,"day":86400,"days":86400,"week":604800,"weeks":604800,"month":2592000,"months":2592000}
    matches=re.findall(r"(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)",text)
    if not matches: raise ValueError
    total=sum(int(n)*units[u] for n,u in matches)
    if total<30 or total>365*86400: raise ValueError
    return total

def selected(context): return int(context.user_data.get('gm_group_id') or 0)


GROUP_VARIABLES = (
    "{ID} = user ID\n"
    "{NAME} = first name\n"
    "{SURNAME} = surname\n"
    "{NAMESURNAME} = full name\n"
    "{LANG} = user language\n"
    "{DATE} = current date\n"
    "{TIME} = current time\n"
    "{WEEKDAY} = week day\n"
    "{MENTION} = link to the user profile\n"
    "{USERNAME} = username\n"
    "{GROUPNAME} = group name\n"
    "{RULES} = group rules/description"
)

def group_text_prompt(title: str) -> str:
    return (
        f"📝 {title}\n\n"
        "Seller, send now the message you want to set!\n\n"
        "You can use HTML and:\n"
        f"• {GROUP_VARIABLES.replace(chr(10), chr(10) + '• ')}"
    )

def group_buttons_prompt() -> str:
    return group_buttons_header()

def group_input_keyboard(back_callback: str, *, remove_callback: str, remove_label: str):
    return kb([
        [InlineKeyboardButton(f"🚫 {remove_label}", callback_data=remove_callback)],
        [InlineKeyboardButton("❌ Cancel", callback_data=back_callback)],
    ])

def group_editor_keyboard(prefix: str, item: dict, *, back_callback: str):
    enabled = bool(item.get("enabled", True))
    text_added = bool(item.get("text"))
    media_added = bool(item.get("media") or item.get("media_file_id"))
    buttons_added = bool(item.get("buttons"))

    return kb([
        [InlineKeyboardButton("🔴 Disable" if enabled else "🟢 Enable", callback_data=f"{prefix}_toggle")],
        [
            InlineKeyboardButton("📄 Text", callback_data=f"{prefix}_text"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_seetext"),
        ],
        [
            InlineKeyboardButton("🖼 Media", callback_data=f"{prefix}_media"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_seemedia"),
        ],
        [
            InlineKeyboardButton("🔗 Buttons", callback_data=f"{prefix}_buttons"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_seebuttons"),
        ],
        [InlineKeyboardButton("👀 Full Preview", callback_data=f"{prefix}_preview")],
        [InlineKeyboardButton("⬅ Back", callback_data=back_callback)],
    ])


def group_editor_header(title: str, keyword: str | None, item: dict) -> str:
    status = "🟢 Enabled" if item.get("enabled", True) else "🔴 Disabled"
    text_status = "✅ Added" if item.get("text") else "❌ Not added"
    media_status = "✅ Added" if (item.get("media") or item.get("media_file_id")) else "❌ Not added"
    buttons_count = sum(len(r) for r in (item.get("buttons") or []))
    parts = [f"{title}"]
    if keyword:
        parts += ["", f"Keyword: {keyword}"]
    parts += [
        "",
        "Current Setup",
        "",
        f"Status: {status}",
        f"📄 Text: {text_status}",
        f"🖼 Media: {media_status}",
        f"🔗 Buttons: {buttons_count}",
        "",
        "Use the options below to add, replace, preview, or remove each part.",
    ]
    return "\n".join(parts)

async def groups_home(q,owner):
    groups=[x for x in await get_channels(owner) if int(x.get('chat_id',0))<0]
    rows=[[InlineKeyboardButton(f"👥 {str(x.get('title') or 'Group')[:35]}",callback_data=f"gm_select_{x['chat_id']}")] for x in groups]
    rows.append([InlineKeyboardButton('⬅ Admin Panel',callback_data='a_home')])
    await q.edit_message_text('🛡 GROUP MANAGER\n\nSelect a connected group. Settings and messages are saved separately for each selected group.',reply_markup=kb(rows))

async def group_home(q,context,owner):
    gid=selected(context); groups=await get_channels(owner); ch=next((x for x in groups if int(x.get('chat_id',0))==gid),None)
    if not ch: return await groups_home(q,owner)
    await get_group(owner,gid,ch.get('title') or 'Group')
    text=f"🛡 GROUP MANAGER\n\n👥 Group: {ch.get('title') or 'Group'}\n🆔 ID: {gid}\n🟢 Bot: Connected\n\nAll settings below apply only to this group."
    rows=[
        [InlineKeyboardButton('👋 Welcome Message',callback_data='gm_welcome')],
        [InlineKeyboardButton('💬 Auto Reply',callback_data='gm_auto')],
        [InlineKeyboardButton('🔗 Forced Join',callback_data='gm_forced_join')],
        [InlineKeyboardButton('🛡 Anti-Spam',callback_data='gm_as'),InlineKeyboardButton('🌊 Anti-Flood',callback_data='gm_af')],
        [InlineKeyboardButton('🚫 Banned Words',callback_data='gm_bw'),InlineKeyboardButton('⚠️ Warns',callback_data='gm_warns')],
        [InlineKeyboardButton('🗑 Delete Commands',callback_data='gm_mod_commands'),InlineKeyboardButton('💥 Service Messages',callback_data='gm_mod_service')],
        [InlineKeyboardButton('⬅ Groups',callback_data='gm_home')],
    ]
    await q.edit_message_text(text,reply_markup=kb(rows))

def welcome_text(item):
    return '👋 Group Welcome Message\n\nSent when a new member joins the selected group.\n\n'+editor_header('Current Setup',item,variables=None)

def welcome_menu(item):
    enabled=bool(item.get('enabled',False))
    delete_last='✅' if item.get('delete_last_welcome',False) else '❌'
    return kb([
        [InlineKeyboardButton('🔴 Disable' if enabled else '🟢 Enable',callback_data='gm_welcome_toggle')],
        [InlineKeyboardButton('📄 Text',callback_data='gm_welcome_text'),InlineKeyboardButton('👀 See',callback_data='gm_welcome_seetext')],
        [InlineKeyboardButton('🖼 Media',callback_data='gm_welcome_media'),InlineKeyboardButton('👀 See',callback_data='gm_welcome_seemedia')],
        [InlineKeyboardButton('🔗 Buttons',callback_data='gm_welcome_buttons'),InlineKeyboardButton('👀 See',callback_data='gm_welcome_seebuttons')],
        [InlineKeyboardButton('👀 Full Preview',callback_data='gm_welcome_preview')],
        [InlineKeyboardButton(f'🗑 Delete Last Welcome: {delete_last}',callback_data='gm_welcome_delete_last')],
        [InlineKeyboardButton('⬅ Back',callback_data='gm_group')],
    ])


def group_buttons_saved_text(rows):
    lines = []
    for row in rows or []:
        chunks = []
        for item in row or []:
            title = str(item.get("text") or "Button")
            typ = str(item.get("type") or "url")
            value = str(item.get("value") or "")
            if typ == "url":
                target = value
            elif typ == "popup":
                target = f"popup: {value}"
            elif typ == "alert":
                target = f"alert: {value}"
            elif typ == "rules":
                target = "rules"
            elif typ == "share":
                target = f"share: {value}"
            elif typ == "copy":
                target = f"copy: {value}"
            else:
                target = value
            chunks.append(f"{title} - {target}")
        if chunks:
            lines.append(" && ".join(chunks))
    return "\n".join(lines)

async def preview(q,context,owner,item,title='Preview'):
    gid=selected(context)
    if title=='Group Welcome':
        item_key='w'
    elif title=='Auto Reply':
        item_key='a'+str(item.get('id') or '')
    else:
        item_key='t'+str(item.get('id') or '')
    markup=build_group_keyboard(item.get('buttons'),item_key=item_key,preview_group_id=gid)
    text=item.get('text') or f'{title}: no text added.'; media=item.get('media') or []
    if not media:
        m=await q.message.reply_text(text,reply_markup=markup); register_feature_origin(m,text=text,markup=markup); return
    e=media[0]; typ=e.get('type'); fid=e.get('file_id')
    if typ=='photo': m=await q.message.reply_photo(fid,caption=text,reply_markup=markup)
    elif typ=='video': m=await q.message.reply_video(fid,caption=text,reply_markup=markup)
    else: m=await q.message.reply_document(fid,caption=text,reply_markup=markup)
    register_feature_origin(m,text=text,markup=markup)


ACTIONS=[("off","❌ Off"),("warn","❕ Warn"),("kick","❗ Kick"),("mute","🔇 Mute"),("ban","🚫 Ban")]

def _action_rows(prefix,current):
    return [
        [InlineKeyboardButton(label+(" ✅" if current==value else ""),callback_data=f"{prefix}.{value}") for value,label in ACTIONS[:3]],
        [InlineKeyboardButton(label+(" ✅" if current==value else ""),callback_data=f"{prefix}.{value}") for value,label in ACTIONS[3:]],
    ]

async def anti_spam_page(q,owner,gid):
    p=await get_protection(owner,gid); a=p.get("anti_spam") or {}
    rows=[
        [InlineKeyboardButton("📘 Telegram Links",callback_data="gm_as_tg")],
        [InlineKeyboardButton("📩 Forwarding",callback_data="gm_as_fw"),InlineKeyboardButton("💭 Quote",callback_data="gm_as_quote")],
        [InlineKeyboardButton("🔗 Total Links Block",callback_data="gm_as_total")],
        [InlineKeyboardButton("⬅ Back",callback_data="gm_group")],
    ]
    await q.edit_message_text("📩 Anti-Spam\nIn this menu you can decide whether to protect your group from unnecessary links, forwards, and quotes.",reply_markup=kb(rows))

async def anti_flood_page(q,owner,gid):
    p=await get_protection(owner,gid); d=p.get("anti_flood") or {}; act=d.get("action","off")
    duration_key={"warn":"warn_duration_seconds","mute":"mute_duration_seconds","ban":"ban_duration_seconds"}.get(act)
    duration=_duration_text(d.get(duration_key,1800)) if duration_key else ""
    text=(f"🌊 Antiflood\nFrom this menu you can set a punishment for those who send many messages in a short time.\n\n"
          f"Currently, the antiflood is triggered when {d.get('messages',5)} messages are sent within {d.get('seconds',3)} seconds.\n\n"
          f"Punishment: {act.title()}"+(f" {duration}" if duration else "")+f" + {'Deletion' if d.get('delete',True) else 'No Deletion'}")
    rows=[
        [InlineKeyboardButton("📄 Messages",callback_data="gm_af_messages"),InlineKeyboardButton("🕘 Time",callback_data="gm_af_time")],
        *_action_rows("gm_af_action",act),
    ]
    if act in {"warn","mute","ban"}:
        label={"warn":"⚠️ ⏱ Set warn duration","mute":"📢 ⏱ Set mute duration","ban":"🚫 ⏱ Set ban duration"}[act]
        rows.append([InlineKeyboardButton(f"{label} ({duration})",callback_data=f"gm_af_duration.{act}")])
    rows += [[InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete',True) else '❌'}",callback_data="gm_af_delete")],[InlineKeyboardButton("⬅ Back",callback_data="gm_group")]]
    await q.edit_message_text(text,reply_markup=kb(rows)); return True

async def banned_words_page(q,owner,gid):
    p=await get_protection(owner,gid); d=p.get("banned_words") or {}; act=d.get("action","off"); words=d.get("words") or []
    text=(f"🚫 Banned Words\nFrom this menu you can set a punishment for users who use the words you decide to ban.\n\n"
          f"Penalty: {act.title()}\nDeletion: {'Yes ✅' if d.get('delete',True) else 'No ❌'}")
    rows=[
        *_action_rows("gm_bw_action",act),
        [InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete',True) else '❌'}",callback_data="gm_bw_delete")],
        [InlineKeyboardButton("➕ Add",callback_data="gm_bw_add"),InlineKeyboardButton("➖ Remove",callback_data="gm_bw_remove")],
        [InlineKeyboardButton("🔤 List",callback_data="gm_bw_list")],
        [InlineKeyboardButton(f"{len(words)} Banned Words",callback_data="gm_bw_list")],
        [InlineKeyboardButton("⬅ Back",callback_data="gm_group")],
    ]
    await q.edit_message_text(text,reply_markup=kb(rows))

async def warns_page(q,owner,gid):
    p=await get_protection(owner,gid); d=p.get("warns") or {}; act=d.get("action","mute"); mx=int(d.get("max_warns",3) or 3)
    text=(f"⚠️ User warnings\nThe warning system allows you to give warnings to users for incorrect behavior in the group, before actually punishing them.\n\n"
          f"Punishment: {act.title()}\nMax Warns allowed: {mx}")
    rows=[
        [InlineKeyboardButton("📄 Warned List",callback_data="gm_warns_list")],
        [InlineKeyboardButton("❌ Off"+(" ✅" if act=="off" else ""),callback_data="gm_warns_action.off"),
         InlineKeyboardButton("❗ Kick"+(" ✅" if act=="kick" else ""),callback_data="gm_warns_action.kick")],
        [InlineKeyboardButton("🔇 Mute"+(" ✅" if act=="mute" else ""),callback_data="gm_warns_action.mute"),
         InlineKeyboardButton("🚫 Ban"+(" ✅" if act=="ban" else ""),callback_data="gm_warns_action.ban")],
        [InlineKeyboardButton(f"🔇🕘 Set mute duration ({d.get('mute_minutes',30)}m)",callback_data="gm_warns_mute")],
        [InlineKeyboardButton(str(n)+(" ✅" if mx==n else ""),callback_data=f"gm_warns_max.{n}") for n in (2,3,4,5,6)],
        [InlineKeyboardButton("⬅ Back",callback_data="gm_group")],
    ]
    await q.edit_message_text(text,reply_markup=kb(rows))

async def delete_commands_page(q, settings):
    d=settings.get('delete_commands',{})
    admin_prefixes=d.get('admin_prefixes') or d.get('prefixes') or ['/']
    user_prefixes=d.get('user_prefixes') or d.get('prefixes') or ['/']
    def pf(v): return ''.join(v) if v else 'No'
    text=("🤖 Delete commands\n"
          "From this menu you can choose to delete messages containing a command, also based on the symbol with which they begin.\n"
          "    Example: /rules, !rules\n\n"
          f"Admins: {pf(admin_prefixes) if d.get('admins') else 'no'}\n"
          f"Users: {pf(user_prefixes) if d.get('users') else 'no'}")
    rows=[
        [InlineKeyboardButton('Admin',callback_data='gm_dc_admin_label'),
         InlineKeyboardButton('No' if not d.get('admins') else 'Yes ✅',callback_data='gm_dc_admin_toggle'),
         InlineKeyboardButton('/'+(' ✅' if d.get('admins') and admin_prefixes==['/'] else ''),callback_data='gm_dc_admin_prefix./')],
        [InlineKeyboardButton('↪',callback_data='gm_dc_admin_label'),
         InlineKeyboardButton('/!;.'+(' ✅' if d.get('admins') and set(admin_prefixes)==set(['/','!',';','.']) else ''),callback_data='gm_dc_admin_prefix.all'),
         InlineKeyboardButton('!;.'+(' ✅' if d.get('admins') and set(admin_prefixes)==set(['!',';','.']) else ''),callback_data='gm_dc_admin_prefix.no_slash')],
        [InlineKeyboardButton('Users',callback_data='gm_dc_user_label'),
         InlineKeyboardButton('No' if not d.get('users') else 'Yes ✅',callback_data='gm_dc_user_toggle'),
         InlineKeyboardButton('/'+(' ✅' if d.get('users') and user_prefixes==['/'] else ''),callback_data='gm_dc_user_prefix./')],
        [InlineKeyboardButton('↪',callback_data='gm_dc_user_label'),
         InlineKeyboardButton('/!;.'+(' ✅' if d.get('users') and set(user_prefixes)==set(['/','!',';','.']) else ''),callback_data='gm_dc_user_prefix.all'),
         InlineKeyboardButton('!;.'+(' ✅' if d.get('users') and set(user_prefixes)==set(['!',';','.']) else ''),callback_data='gm_dc_user_prefix.no_slash')],
        [InlineKeyboardButton('⬅ Back',callback_data='gm_group')],
    ]
    await q.edit_message_text(text,reply_markup=kb(rows))
    return True

async def handle(self,update,context,q,owner,staff,a,role):
    if not a.startswith('gm_'): return False
    if role!='seller': await q.answer('Only the seller can manage groups.',show_alert=True); return True
    try: await q.answer()
    except Exception: pass
    context.user_data.pop('gm_input', None)
    if a=='gm_home': await groups_home(q,owner); return True
    if a.startswith('gm_select_'):
        context.user_data['gm_group_id']=int(a[len('gm_select_'):]); await group_home(q,context,owner); return True
    if a=='gm_group': await group_home(q,context,owner); return True

    # These three editor-entry callbacks do not need a database read. Handle them
    # before get_group() so the editor opens immediately after the button tap.
    if a=='gm_welcome_text':
        context.user_data['gm_input']='welcome_text'
        await q.edit_message_text(group_text_prompt('Group Welcome Message'),reply_markup=group_input_keyboard('gm_welcome',remove_callback='gm_welcome_rmtext',remove_label='Remove message'))
        return True
    if a=='gm_welcome_media':
        context.user_data['gm_input']='welcome_media'
        await q.edit_message_text(editor_media_prompt('Group Welcome Media'),reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_welcome')]]))
        return True
    if a=='gm_welcome_buttons':
        context.user_data['gm_input']='welcome_buttons'
        await q.edit_message_text(group_buttons_prompt(),reply_markup=group_input_keyboard('gm_welcome',remove_callback='gm_welcome_rmbuttons',remove_label='Remove Keyboard'))
        return True

    gid=selected(context)
    if not gid: await groups_home(q,owner); return True
    doc=await get_group(owner,gid); item=doc.get('welcome') or {}
    if a=='gm_as': await anti_spam_page(q,owner,gid); return True
    if a=='gm_af': await anti_flood_page(q,owner,gid); return True
    if a=='gm_bw': await banned_words_page(q,owner,gid); return True
    if a=='gm_warns': await warns_page(q,owner,gid); return True

    if a=='gm_as_tg':
        p=await get_protection(owner,gid); d=p['anti_spam']['telegram_links']; act=d.get('action','off')
        rows=_action_rows('gm_as_tg_action',act)+[
            [InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete') else '❌'}",callback_data='gm_as_tg_delete')],
            [InlineKeyboardButton(f"🎯 Username Antispam {'✅' if d.get('username_antispam') else '❌'}",callback_data='gm_as_tg_usernames')],
            [InlineKeyboardButton(f"🤖 Bots Antispam {'✅' if d.get('bots_antispam') else '❌'}",callback_data='gm_as_tg_bots')],
            [InlineKeyboardButton('⬅ Back',callback_data='gm_as'),InlineKeyboardButton('☀️ Exceptions',callback_data='gm_exceptions')],
        ]
        await q.edit_message_text(f"📘 Telegram links\nFrom this menu you can set a punishment for users who send messages that contain Telegram links.\n\nPenalty: {act.title()}\nDeletion: {'Yes ✅' if d.get('delete') else 'No ❌'}",reply_markup=kb(rows)); return True

    if a=='gm_as_fw':
        p=await get_protection(owner,gid); d=p['anti_spam']['forwarding']
        rows=[
            [InlineKeyboardButton(f"📣 Channels {'✅' if d.get('channels') else '❌'}",callback_data='gm_as_fw_toggle.channels'),InlineKeyboardButton(f"👥 Groups {'✅' if d.get('groups') else '❌'}",callback_data='gm_as_fw_toggle.groups')],
            [InlineKeyboardButton(f"👤 Users {'✅' if d.get('users') else '❌'}",callback_data='gm_as_fw_toggle.users'),InlineKeyboardButton(f"🤖 Bots {'✅' if d.get('bots') else '❌'}",callback_data='gm_as_fw_toggle.bots')],
            *_action_rows('gm_as_fw_action',d.get('action','off')),
            [InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete') else '❌'}",callback_data='gm_as_fw_delete')],
            [InlineKeyboardButton('⬅ Back',callback_data='gm_as'),InlineKeyboardButton('☀️ Exceptions',callback_data='gm_exceptions')],
        ]
        await q.edit_message_text("📩 Forwarding\nSelect punishment for users who forward messages in the group.",reply_markup=kb(rows)); return True

    if a=='gm_as_quote':
        p=await get_protection(owner,gid); d=p['anti_spam']['quote']
        rows=_action_rows('gm_as_quote_action',d.get('action','off'))+[
            [InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete') else '❌'}",callback_data='gm_as_quote_delete')],
            [InlineKeyboardButton('⬅ Back',callback_data='gm_as'),InlineKeyboardButton('☀️ Exceptions',callback_data='gm_exceptions')],
        ]
        await q.edit_message_text("💭 Quote\nChoose how quoted/forwarded quote messages should be handled.",reply_markup=kb(rows)); return True

    if a=='gm_as_total':
        p=await get_protection(owner,gid); d=p['anti_spam']['total_links']; act=d.get('action','off')
        rows=_action_rows('gm_as_total_action',act)+[
            [InlineKeyboardButton(f"🗑 Delete Messages {'✅' if d.get('delete') else '❌'}",callback_data='gm_as_total_delete')],
            [InlineKeyboardButton('⬅ Back',callback_data='gm_as'),InlineKeyboardButton('☀️ Exceptions',callback_data='gm_exceptions')],
        ]
        await q.edit_message_text(f"🔗 TOTAL LINKS BLOCK\nChoose the punishment for those who send any kind of link.\n\nPenalty: {act.title()}\nDeletion: {'Yes ✅' if d.get('delete') else 'No ❌'}",reply_markup=kb(rows)); return True

    if a=='gm_exceptions':
        await q.answer('Exceptions will be added in the next refinement.',show_alert=True); return True

    if a.startswith('gm_as_tg_action.'):
        await set_protection(owner,gid,'anti_spam.telegram_links.action',a.rsplit('.',1)[1]); return await handle(self,update,context,q,owner,staff,'gm_as_tg',role)
    if a=='gm_as_tg_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.telegram_links.delete',not p['anti_spam']['telegram_links'].get('delete')); return await handle(self,update,context,q,owner,staff,'gm_as_tg',role)
    if a=='gm_as_tg_usernames':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.telegram_links.username_antispam',not p['anti_spam']['telegram_links'].get('username_antispam')); return await handle(self,update,context,q,owner,staff,'gm_as_tg',role)
    if a=='gm_as_tg_bots':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.telegram_links.bots_antispam',not p['anti_spam']['telegram_links'].get('bots_antispam')); return await handle(self,update,context,q,owner,staff,'gm_as_tg',role)

    if a.startswith('gm_as_fw_toggle.'):
        key=a.rsplit('.',1)[1]; p=await get_protection(owner,gid); await set_protection(owner,gid,f'anti_spam.forwarding.{key}',not p['anti_spam']['forwarding'].get(key)); return await handle(self,update,context,q,owner,staff,'gm_as_fw',role)
    if a.startswith('gm_as_fw_action.'):
        await set_protection(owner,gid,'anti_spam.forwarding.action',a.rsplit('.',1)[1]); return await handle(self,update,context,q,owner,staff,'gm_as_fw',role)
    if a=='gm_as_fw_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.forwarding.delete',not p['anti_spam']['forwarding'].get('delete')); return await handle(self,update,context,q,owner,staff,'gm_as_fw',role)

    if a.startswith('gm_as_quote_action.'):
        await set_protection(owner,gid,'anti_spam.quote.action',a.rsplit('.',1)[1]); return await handle(self,update,context,q,owner,staff,'gm_as_quote',role)
    if a=='gm_as_quote_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.quote.delete',not p['anti_spam']['quote'].get('delete')); return await handle(self,update,context,q,owner,staff,'gm_as_quote',role)

    if a.startswith('gm_as_total_action.'):
        await set_protection(owner,gid,'anti_spam.total_links.action',a.rsplit('.',1)[1]); return await handle(self,update,context,q,owner,staff,'gm_as_total',role)
    if a=='gm_as_total_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_spam.total_links.delete',not p['anti_spam']['total_links'].get('delete')); return await handle(self,update,context,q,owner,staff,'gm_as_total',role)

    if a.startswith('gm_af_action.'):
        await set_protection(owner,gid,'anti_flood.action',a.rsplit('.',1)[1]); return await anti_flood_page(q,owner,gid) or True
    if a=='gm_af_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'anti_flood.delete',not p['anti_flood'].get('delete')); await anti_flood_page(q,owner,gid); return True
    if a.startswith('gm_af_duration.'):
        kind=a.rsplit('.',1)[1]; context.user_data['gm_input']=f'af_duration_{kind}'
        p=await get_protection(owner,gid); d=p.get('anti_flood') or {}; key={'warn':'warn_duration_seconds','mute':'mute_duration_seconds','ban':'ban_duration_seconds'}[kind]
        await q.edit_message_text(f"Send now the duration of the chosen punishment ({kind.title()})\n\nMinimum: 30 seconds\nMaximum: 365 days\n\nExample of format: 3 month 2 days 12 hours 4 minutes 34 seconds\n\nCurrent duration: {_duration_text(d.get(key,1800))}",reply_markup=kb([[InlineKeyboardButton('0 Remove duration',callback_data=f'gm_af_remove_duration.{kind}')],[InlineKeyboardButton('❌ Cancel',callback_data='gm_af')]])); return True
    if a.startswith('gm_af_remove_duration.'):
        kind=a.rsplit('.',1)[1]; key={'warn':'warn_duration_seconds','mute':'mute_duration_seconds','ban':'ban_duration_seconds'}[kind]
        await set_protection(owner,gid,f'anti_flood.{key}',0); return await anti_flood_page(q,owner,gid)
    if a=='gm_af_messages':
        context.user_data['gm_input']='af_messages'; await q.edit_message_text('🌊 Antiflood\n\nSend the maximum number of messages allowed before antiflood triggers.\nExample: 5',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_af')]])); return True
    if a=='gm_af_time':
        context.user_data['gm_input']='af_time'; await q.edit_message_text('🌊 Antiflood\n\nSend the time window in seconds.\nExample: 3',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_af')]])); return True

    if a.startswith('gm_bw_action.'):
        await set_protection(owner,gid,'banned_words.action',a.rsplit('.',1)[1]); await banned_words_page(q,owner,gid); return True
    if a=='gm_bw_delete':
        p=await get_protection(owner,gid); await set_protection(owner,gid,'banned_words.delete',not p['banned_words'].get('delete')); await banned_words_page(q,owner,gid); return True
    if a=='gm_bw_add':
        context.user_data['gm_input']='bw_add'; await q.edit_message_text('➕ Add Banned Word\n\nSend one word or phrase to add.',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_bw')]])); return True
    if a=='gm_bw_remove':
        context.user_data['gm_input']='bw_remove'; await q.edit_message_text('➖ Remove Banned Word\n\nSend the exact word or phrase to remove.',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_bw')]])); return True
    if a=='gm_bw_list':
        p=await get_protection(owner,gid); words=p['banned_words'].get('words') or []; body='\n'.join(f'• {w}' for w in words) if words else 'No banned words added.'
        await q.edit_message_text('🔤 Banned Words List\n\n'+body,reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_bw')]])); return True

    if a.startswith('gm_warns_action.'):
        await set_protection(owner,gid,'warns.action',a.rsplit('.',1)[1]); await warns_page(q,owner,gid); return True
    if a.startswith('gm_warns_max.'):
        await set_protection(owner,gid,'warns.max_warns',int(a.rsplit('.',1)[1])); await warns_page(q,owner,gid); return True
    if a=='gm_warns_mute':
        context.user_data['gm_input']='warns_mute'; await q.edit_message_text('🔇 Set mute duration\n\nSend duration in minutes.\nExample: 30',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_warns')]])); return True
    if a=='gm_warns_list':
        data=await warned_list(owner,gid); body='\n'.join(f'• {uid}: {count} warn(s)' for uid,count in data.items()) if data else 'No warned users.'
        await q.edit_message_text('📄 Warned List\n\n'+body,reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_warns')]])); return True
    # Welcome callbacks are answered immediately at the top of handle().
    if a=='gm_welcome':
        await q.edit_message_text(welcome_text(item),reply_markup=welcome_menu(item))
        return True
    if a=='gm_welcome_toggle':
        await update_welcome(owner,gid,enabled=not item.get('enabled',False))
        doc=await get_group(owner,gid)
        await q.edit_message_text(welcome_text(doc['welcome']),reply_markup=welcome_menu(doc['welcome']))
        return True
    if a=='gm_welcome_delete_last':
        await update_welcome(owner,gid,delete_last_welcome=not item.get('delete_last_welcome',False))
        doc=await get_group(owner,gid)
        await q.edit_message_text(welcome_text(doc['welcome']),reply_markup=welcome_menu(doc['welcome']))
        return True
    if a=='gm_welcome_rmtext':
        await update_welcome(owner,gid,text='')
        doc=await get_group(owner,gid)
        await q.edit_message_text(group_editor_header('👋 Group Welcome Message', None, doc['welcome']), reply_markup=group_editor_keyboard('gm_welcome', doc['welcome'], back_callback='gm_group'))
        return True
    if a=='gm_welcome_rmbuttons':
        await update_welcome(owner,gid,buttons=[])
        doc=await get_group(owner,gid)
        await q.edit_message_text(group_editor_header('👋 Group Welcome Message', None, doc['welcome']), reply_markup=group_editor_keyboard('gm_welcome', doc['welcome'], back_callback='gm_group'))
        return True
    if a=='gm_welcome_seetext':
        if not item.get('text'):
            await q.answer('No text added.', show_alert=True)
            return True
        await q.message.reply_text(item.get('text') or '', parse_mode='HTML')
        return True
    if a=='gm_welcome_seemedia':
        media=item.get('media') or []
        if not media and not item.get('media_file_id'):
            await q.answer('No media added.', show_alert=True)
            return True
        await preview(q,context,owner,{**item,'text':'','buttons':[]},'Group Welcome Media')
        return True
    if a=='gm_welcome_seebuttons':
        if not item.get('buttons'):
            await q.answer('No buttons added.', show_alert=True)
            return True
        # Header contains the exact configured title/target syntax. Under it,
        # show only the real button preview; no Add More/Delete controls.
        markup=build_group_keyboard(item.get('buttons'),item_key='w',preview_group_id=gid)
        saved=group_buttons_saved_text(item.get('buttons'))
        header='🔗 Current Buttons\n\n' + (saved or 'No buttons set.')
        # Keep only the configured button preview plus Back to the Welcome main page.
        preview_rows=[list(row) for row in (markup.inline_keyboard if markup else [])]
        preview_rows.append([InlineKeyboardButton('⬅ Back',callback_data='gm_welcome')])
        markup=InlineKeyboardMarkup(preview_rows)
        await q.message.reply_text(header, reply_markup=markup, disable_web_page_preview=True)
        return True
    if a=='gm_welcome_preview':
        await preview(q,context,owner,item,'Group Welcome')
        return True
    if a=='gm_auto':
        items=await list_auto_replies(owner,gid); rows=[[InlineKeyboardButton(f"💬 {x.get('keyword','Keyword')}",callback_data=f"gm_ar_{x['id']}")] for x in items]; rows += [[InlineKeyboardButton('➕ Add Keyword',callback_data='gm_ar_add')],[InlineKeyboardButton('⬅ Back',callback_data='gm_group')]]; await q.edit_message_text('💬 Group Auto Reply\n\nKeyword replies are saved only for this selected group.',reply_markup=kb(rows)); return True
    if a=='gm_ar_add': context.user_data['gm_input']='ar_keyword'; await q.edit_message_text('➕ Add Auto Reply\n\nSend a keyword or phrase.',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_auto')]])); return True
    if a.startswith('gm_ar_') and a not in {'gm_ar_add'}:
        rest=a[len('gm_ar_'):]; parts=rest.rsplit('_',1); rid=parts[0]; action=parts[1] if len(parts)>1 and parts[1] in {'text','media','buttons','preview','toggle','rmtext','rmbuttons','seetext','seemedia','seebuttons'} else 'open'; rid=rest if action=='open' else rid
        ar=await get_auto_reply(owner,gid,rid)
        if not ar: await q.answer('Auto Reply not found.',show_alert=True); return True
        if action=='open': await q.edit_message_text(group_editor_header('💬 Group Auto Reply', ar.get('keyword',''), ar), reply_markup=group_editor_keyboard(f'gm_ar_{rid}', ar, back_callback='gm_auto')); return True
        if action=='toggle': ar['enabled']=not ar.get('enabled',True); await save_auto_reply(owner,gid,ar); q.data=f'gm_ar_{rid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='text': context.user_data['gm_input']=f'ar_{rid}_text'; await q.edit_message_text(group_text_prompt('Group Auto Reply'),reply_markup=group_input_keyboard(f'gm_ar_{rid}',remove_callback=f'gm_ar_{rid}_rmtext',remove_label='Remove message')); return True
        if action=='media': context.user_data['gm_input']=f'ar_{rid}_media'; await q.edit_message_text(editor_media_prompt('Group Auto Reply Media'),reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data=f'gm_ar_{rid}')]])); return True
        if action=='buttons': context.user_data['gm_input']=f'ar_{rid}_buttons'; await q.edit_message_text(group_buttons_prompt(),reply_markup=group_input_keyboard(f'gm_ar_{rid}',remove_callback=f'gm_ar_{rid}_rmbuttons',remove_label='Remove Keyboard')); return True
        if action=='rmtext': ar['text']=''; await save_auto_reply(owner,gid,ar); await q.answer('Auto Reply message removed.'); q.data=f'gm_ar_{rid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='rmbuttons': ar['buttons']=[]; await save_auto_reply(owner,gid,ar); await q.answer('Auto Reply keyboard removed.'); q.data=f'gm_ar_{rid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='seetext':
            if not ar.get('text'): await q.answer('No text added.', show_alert=True); return True
            await q.message.reply_text(ar.get('text') or '', parse_mode='HTML'); return True
        if action=='seemedia':
            media=ar.get('media') or []
            if not media and not ar.get('media_file_id'): await q.answer('No media added.', show_alert=True); return True
            await preview(q,context,owner,{**ar,'text':'','buttons':[]},'Auto Reply Media'); return True
        if action=='seebuttons':
            if not ar.get('buttons'): await q.answer('No buttons added.', show_alert=True); return True
            markup=build_group_keyboard(ar.get('buttons'),item_key='a'+str(ar.get('id') or ''),preview_group_id=gid)
            saved=group_buttons_saved_text(ar.get('buttons'))
            await q.message.reply_text(saved or 'Choose an option:', reply_markup=markup, disable_web_page_preview=True); return True
        if action=='preview': await preview(q,context,owner,ar,'Auto Reply'); return True
    if a=='gm_templates':
        items=await list_templates(owner,gid); rows=[[InlineKeyboardButton(f"📝 {x.get('keyword','Template')}",callback_data=f"gm_tpl_{x['id']}")] for x in items]; rows += [[InlineKeyboardButton('➕ Add Reply Template',callback_data='gm_tpl_add')],[InlineKeyboardButton('⬅ Back',callback_data='gm_group')]]; await q.edit_message_text('📝 Group Reply Templates\n\nTemplates are saved only for this selected group.',reply_markup=kb(rows)); return True
    if a=='gm_tpl_add': context.user_data['gm_input']='tpl_keyword'; await q.edit_message_text('➕ Add Reply Template\n\nSend a unique keyword.',reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data='gm_templates')]])); return True
    if a.startswith('gm_tpl_') and a not in {'gm_tpl_add'}:
        rest=a[len('gm_tpl_'):]; parts=rest.rsplit('_',1); tid=parts[0]; action=parts[1] if len(parts)>1 and parts[1] in {'text','media','buttons','preview','toggle','rmtext','rmbuttons','seetext','seemedia','seebuttons'} else 'open'; tid=rest if action=='open' else tid
        it=await get_template(owner,gid,tid)
        if not it: await q.answer('Template not found.',show_alert=True); return True
        if action=='open': await q.edit_message_text(group_editor_header('📝 Group Reply Template', it.get('keyword',''), it), reply_markup=group_editor_keyboard(f'gm_tpl_{tid}', it, back_callback='gm_templates')); return True
        if action=='toggle': it['enabled']=not it.get('enabled',True); await save_template(owner,gid,it); q.data=f'gm_tpl_{tid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='text': context.user_data['gm_input']=f'tpl_{tid}_text'; await q.edit_message_text(group_text_prompt('Group Reply Template'),reply_markup=group_input_keyboard(f'gm_tpl_{tid}',remove_callback=f'gm_tpl_{tid}_rmtext',remove_label='Remove message')); return True
        if action=='media': context.user_data['gm_input']=f'tpl_{tid}_media'; await q.edit_message_text(editor_media_prompt('Group Reply Template Media'),reply_markup=kb([[InlineKeyboardButton('⬅ Back',callback_data=f'gm_tpl_{tid}')]])); return True
        if action=='buttons': context.user_data['gm_input']=f'tpl_{tid}_buttons'; await q.edit_message_text(group_buttons_prompt(),reply_markup=group_input_keyboard(f'gm_tpl_{tid}',remove_callback=f'gm_tpl_{tid}_rmbuttons',remove_label='Remove Keyboard')); return True
        if action=='rmtext': it['text']=''; await save_template(owner,gid,it); await q.answer('Reply Template message removed.'); q.data=f'gm_tpl_{tid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='rmbuttons': it['buttons']=[]; await save_template(owner,gid,it); await q.answer('Reply Template keyboard removed.'); q.data=f'gm_tpl_{tid}'; return await handle(self,update,context,q,owner,staff,q.data,role)
        if action=='seetext':
            if not it.get('text'): await q.answer('No text added.', show_alert=True); return True
            await q.message.reply_text(it.get('text') or '', parse_mode='HTML'); return True
        if action=='seemedia':
            media=it.get('media') or []
            if not media and not it.get('media_file_id'): await q.answer('No media added.', show_alert=True); return True
            await preview(q,context,owner,{**it,'text':'','buttons':[]},'Reply Template Media'); return True
        if action=='seebuttons':
            if not it.get('buttons'): await q.answer('No buttons added.', show_alert=True); return True
            markup=build_group_keyboard(it.get('buttons'),item_key='t'+str(it.get('id') or ''),preview_group_id=gid)
            saved=group_buttons_saved_text(it.get('buttons'))
            await q.message.reply_text(saved or 'Choose an option:', reply_markup=markup, disable_web_page_preview=True); return True
        if action=='preview': await preview(q,context,owner,it,'Reply Template'); return True
    if a=='gm_mod':
        s=await get_moderation(owner,gid); mark=lambda v:'✅' if v else '❌'; rows=[[InlineKeyboardButton(f"{mark(s.get('enabled',True))} Moderation Master Switch",callback_data='gm_mod_master')],[InlineKeyboardButton('🗑 Delete Commands',callback_data='gm_mod_commands')],[InlineKeyboardButton('🔗 Link Protection',callback_data='gm_mod_links')],[InlineKeyboardButton('📦 Forwarded Media',callback_data='gm_mod_forwarded')],[InlineKeyboardButton('💥 Service Messages',callback_data='gm_mod_service')],[InlineKeyboardButton('🛡 Safety Settings',callback_data='gm_mod_safety')],[InlineKeyboardButton('♻️ Reset Settings',callback_data='gm_mod_reset')],[InlineKeyboardButton('⬅ Back',callback_data='gm_group')]]; await q.edit_message_text('🗑 Message Moderation\n\nThese deletion settings apply only to the selected group.',reply_markup=kb(rows)); return True
    if a in {'gm_mod_commands','gm_mod_links','gm_mod_forwarded','gm_mod_service','gm_mod_safety'}:
        s=await get_moderation(owner,gid); mark=lambda v:'✅' if v else '❌'
        if a=='gm_mod_commands':
            return await delete_commands_page(q,s)
        elif a=='gm_mod_links':
            d=s.get('link_protection',{}); keys=[('enabled','Protection'),('all_links','All Links'),('telegram','Telegram'),('instagram','Instagram'),('youtube','YouTube'),('facebook','Facebook'),('x_twitter','X / Twitter'),('tiktok','TikTok'),('discord','Discord')]; rows=[[InlineKeyboardButton(f"{mark(d.get(k))} {label}",callback_data=f'gm_set_link_protection.{k}')] for k,label in keys]; title='🔗 Link Protection'
        elif a=='gm_mod_forwarded':
            d=s.get('forwarded_media',{}); keys=[('enabled','Protection'),('photo','Photo'),('video','Video'),('animation','Animation'),('document','Document'),('audio','Audio'),('voice','Voice'),('sticker','Sticker'),('video_note','Video Note')]; rows=[[InlineKeyboardButton(f"{mark(d.get(k))} {label}",callback_data=f'gm_set_forwarded_media.{k}')] for k,label in keys]; title='📦 Forwarded Media'
        elif a=='gm_mod_service':
            d=s.get('service_messages',{}); keys=[('join','Join'),('exit','Exit'),('photos','Group Photo'),('title','Group Title'),('pinned','Pinned'),('topic','Topic'),('boost','Boost'),('video_chats','Video Chats'),('checklist','Checklist'),('community','Community')]; rows=[[InlineKeyboardButton(f"{mark(d.get(k))} {label}",callback_data=f'gm_set_service_messages.{k}')] for k,label in keys]; title='💥 Service Messages'
        else:
            rows=[[InlineKeyboardButton(f"{mark(s.get('ignore_admins'))} Ignore Admins",callback_data='gm_set_ignore_admins')],[InlineKeyboardButton(f"{mark(s.get('ignore_owner'))} Ignore Owner",callback_data='gm_set_ignore_owner')]]; title='🛡 Safety Settings'
        rows.append([InlineKeyboardButton('⬅ Back',callback_data='gm_group')]); await q.edit_message_text(title+'\n\nSettings apply only to this selected group.',reply_markup=kb(rows)); return True
    if a in {'gm_dc_admin_label','gm_dc_user_label'}:
        await q.answer(); return True
    if a=='gm_dc_admin_toggle':
        st=await get_moderation(owner,gid); cur=bool((st.get('delete_commands') or {}).get('admins'))
        st=await set_moderation_value(owner,gid,'delete_commands.admins',not cur)
        return await delete_commands_page(q,st)
    if a=='gm_dc_user_toggle':
        st=await get_moderation(owner,gid); cur=bool((st.get('delete_commands') or {}).get('users'))
        st=await set_moderation_value(owner,gid,'delete_commands.users',not cur)
        return await delete_commands_page(q,st)
    if a.startswith('gm_dc_admin_prefix.'):
        mode=a.rsplit('.',1)[1]
        prefixes={'/':['/'],'all':['/','!',';','.'],'no_slash':['!',';','.']}[mode]
        st=await set_moderation_values(owner,gid,{'delete_commands.admins':True,'delete_commands.admin_prefixes':prefixes})
        return await delete_commands_page(q,st)
    if a.startswith('gm_dc_user_prefix.'):
        mode=a.rsplit('.',1)[1]
        prefixes={'/':['/'],'all':['/','!',';','.'],'no_slash':['!',';','.']}[mode]
        st=await set_moderation_values(owner,gid,{'delete_commands.users':True,'delete_commands.user_prefixes':prefixes})
        return await delete_commands_page(q,st)
    if a.startswith('gm_set_'):
        path=a[len('gm_set_'):]; s=await get_moderation(owner,gid)
        cur=s
        for part in path.split('.'):
            cur=cur.get(part,{}) if isinstance(cur,dict) else False
        await set_moderation_value(owner,gid,path,not bool(cur))
        parent={'delete_commands':'gm_mod_commands','link_protection':'gm_mod_links','forwarded_media':'gm_mod_forwarded','service_messages':'gm_mod_service'}.get(path.split('.')[0],'gm_mod_safety')
        return await handle(self,update,context,q,owner,staff,parent,role)
    if a=='fj_editor':
        from handlers.clone.forced_join_runtime import forced_join_message_editor
        await forced_join_message_editor(q,context)
        return True
    if a=='gm_forced_join':
        from handlers.clone.forced_join_runtime import forced_join_page
        await forced_join_page(q,context)
        return True
    if a.startswith('fj_toggle:'):
        from handlers.clone.forced_join_runtime import forced_join_toggle_callback
        return await forced_join_toggle_callback(update,context)

    return True

async def handle_text(self,update,context):
    mode=context.user_data.get('gm_input'); gid=selected(context)
    if not mode or not gid: return False
    owner=self.owner(context)
    if update.effective_user.id!=self.seller_account(context): return False
    text=(update.effective_message.text or '').strip()
    if mode=='af_messages':
        try: value=max(2,min(50,int(text)))
        except ValueError: await update.effective_message.reply_text('❌ Send a number from 2 to 50.'); return True
        await set_protection(owner,gid,'anti_flood.messages',value); context.user_data.pop('gm_input',None)
        await update.effective_message.reply_text('✅ Antiflood message limit saved.'); return True
    if mode=='af_time':
        try: value=max(1,min(60,int(text)))
        except ValueError: await update.effective_message.reply_text('❌ Send seconds from 1 to 60.'); return True
        await set_protection(owner,gid,'anti_flood.seconds',value); context.user_data.pop('gm_input',None)
        await update.effective_message.reply_text('✅ Antiflood time saved.'); return True
    if mode.startswith('af_duration_'):
        kind=mode.rsplit('_',1)[1]; key={'warn':'warn_duration_seconds','mute':'mute_duration_seconds','ban':'ban_duration_seconds'}[kind]
        try: value=_parse_duration(text)
        except ValueError: await update.effective_message.reply_text('❌ Invalid duration. Use e.g. 30 minutes or 3 month 2 days 12 hours.'); return True
        await set_protection(owner,gid,f'anti_flood.{key}',value); context.user_data.pop('gm_input',None)
        await update.effective_message.reply_text(f'✅ {kind.title()} duration saved.'); return True
    if mode=='bw_add':
        await add_banned_word(owner,gid,text); context.user_data.pop('gm_input',None); await update.effective_message.reply_text('✅ Banned word added.'); return True
    if mode=='bw_remove':
        await remove_banned_word(owner,gid,text); context.user_data.pop('gm_input',None); await update.effective_message.reply_text('✅ Banned word removed.'); return True
    if mode=='warns_mute':
        try: value=max(1,min(10080,int(text)))
        except ValueError: await update.effective_message.reply_text('❌ Send mute duration in minutes.'); return True
        await set_protection(owner,gid,'warns.mute_minutes',value); context.user_data.pop('gm_input',None); await update.effective_message.reply_text('✅ Mute duration saved.'); return True
    if mode=='welcome_text': await update_welcome(owner,gid,text=text); back='gm_welcome'; msg='✅ Welcome text saved.'
    elif mode=='welcome_buttons':
        try: buttons=parse_group_buttons(text)
        except ValueError as e: await update.effective_message.reply_text(f'❌ {e}'); return True
        await update_welcome(owner,gid,buttons=buttons); back='gm_welcome'; msg='✅ Buttons saved.'
    elif mode=='ar_keyword':
        item = await save_auto_reply(owner,gid,{'keyword':text,'enabled':True,'text':'','media':[],'buttons':[]})
        context.user_data.pop('gm_input',None)
        rid = item['id']
        await update.effective_message.reply_text(
            group_editor_header('💬 Group Auto Reply', item.get('keyword',''), item),
            reply_markup=group_editor_keyboard(f'gm_ar_{rid}', item, back_callback='gm_auto'),
        )
        return True
    elif mode=='tpl_keyword':
        item = await save_template(owner,gid,{'keyword':text,'enabled':True,'text':'','media':[],'buttons':[]})
        context.user_data.pop('gm_input',None)
        tid = item['id']
        await update.effective_message.reply_text(
            group_editor_header('📝 Group Reply Template', item.get('keyword',''), item),
            reply_markup=group_editor_keyboard(f'gm_tpl_{tid}', item, back_callback='gm_templates'),
        )
        return True
    elif mode.startswith('ar_') or mode.startswith('tpl_'):
        kind,rid,field=mode.split('_',2); item=await (get_auto_reply(owner,gid,rid) if kind=='ar' else get_template(owner,gid,rid))
        if not item: context.user_data.pop('gm_input',None); return True
        if field=='buttons':
            try: item['buttons']=parse_group_buttons(text)
            except ValueError as e: await update.effective_message.reply_text(f'❌ {e}'); return True
        else: item['text']=text
        if kind=='ar': await save_auto_reply(owner,gid,item); back=f'gm_ar_{rid}'
        else: await save_template(owner,gid,item); back=f'gm_tpl_{rid}'
        msg='✅ Saved.'
    else: return False
    context.user_data.pop('gm_input',None); await update.effective_message.reply_text(msg,reply_markup=kb([[InlineKeyboardButton('⬅ Continue',callback_data=back)]])); return True

async def handle_media(self,update,context):
    mode=context.user_data.get('gm_input') or ''
    if not selected(context) or not (mode=='welcome_media' or mode.endswith('_media')): return False
    m=update.effective_message; entry=None
    if m.photo: entry={'type':'photo','file_id':m.photo[-1].file_id}
    elif m.video: entry={'type':'video','file_id':m.video.file_id}
    elif m.document: entry={'type':'document','file_id':m.document.file_id}
    if not entry: return False
    owner=self.owner(context); gid=selected(context)
    if mode=='welcome_media': await update_welcome(owner,gid,media=[entry]); back='gm_welcome'
    else:
        kind,rid,_=mode.split('_',2); item=await (get_auto_reply(owner,gid,rid) if kind=='ar' else get_template(owner,gid,rid)); item['media']=[entry]
        if kind=='ar': await save_auto_reply(owner,gid,item); back=f'gm_ar_{rid}'
        else: await save_template(owner,gid,item); back=f'gm_tpl_{rid}'
    context.user_data.pop('gm_input',None); await m.reply_text('✅ Media saved.',reply_markup=kb([[InlineKeyboardButton('⬅ Continue',callback_data=back)]])); return True
