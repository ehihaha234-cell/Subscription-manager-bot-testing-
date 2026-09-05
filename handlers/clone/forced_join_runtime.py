import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from handlers.common.editor_engine import parse_editor_buttons, build_editor_keyboard, editor_media_prompt, FEATURE_CALLBACKS
from database.forced_join import list_required, get_required, toggle_required, remove_required, update_invite, save_pending_request, list_pending_requests, remove_pending_request
from database.seller_data import get_channels
from database.forced_join import get_forced_join_editor, set_forced_join_editor, get_forced_join_enabled, set_forced_join_enabled, get_forced_join_editor_enabled, set_forced_join_editor_enabled

logger=logging.getLogger(__name__)

def _kb(rows):
    return InlineKeyboardMarkup(rows)

async def _required_status(bot, user_id, required):
    for item in required:
        if not item.get("enabled", True):
            continue
        try:
            member=await bot.get_chat_member(int(item["chat_id"]), int(user_id))
            if member.status in {"left","kicked"}:
                return False, item
        except Exception:
            # If the bot cannot verify a required chat, fail closed.
            return False, item
    return True, None


def _render_forced_join_variables(value: str, user) -> str:
    """Render the same user/date variables advertised by the Forced Join editor."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    first = str(getattr(user, "first_name", "") or "")
    last = str(getattr(user, "last_name", "") or "")
    full_name = " ".join(x for x in (first, last) if x).strip() or str(getattr(user, "username", "") or "User")
    username_raw = str(getattr(user, "username", "") or "").lstrip("@")
    user_id = str(getattr(user, "id", "") or "")
    values = {
        "{ID}": user_id,
        "{NAME}": first or full_name,
        "{FIRSTNAME}": first,
        "{SURNAME}": last,
        "{NAMESURNAME}": full_name,
        "{USERNAME}": f"@{username_raw}" if username_raw else "",
        "{MENTION}": f"tg://user?id={user_id}" if user_id else "",
        "{LANG}": str(getattr(user, "language_code", "") or ""),
        "{DATE}": now.strftime("%d %b %Y"),
        "{TIME}": now.strftime("%I:%M %p"),
        "{WEEKDAY}": now.strftime("%A"),
    }
    rendered = str(value or "")
    for token, replacement in values.items():
        rendered = rendered.replace(token, replacement)
    return rendered


async def _send_forced_join_approval_message(bot, owner, user_id, user_chat_id=None):
    if not await get_forced_join_editor_enabled(owner):
        return
    item=await get_forced_join_editor(owner)
    if not item:
        return
    raw_text=item.get("text") or ""
    try:
        user = await bot.get_chat(int(user_id))
    except Exception:
        # Approval messages can be sent to join-request users before /start.
        # Keep delivery working even if Telegram does not expose full profile data.
        user = type("JoinRequestUser", (), {"id": user_id, "first_name": "", "last_name": "", "username": "", "language_code": ""})()
    text=_render_forced_join_variables(raw_text, user)
    media=item.get("media") or []
    buttons=item.get("buttons") or []
    markup=_approval_markup(buttons)
    # IMPORTANT: use the exact same private-chat target as the Forced Join
    # DM: ChatJoinRequest.user_chat_id. This is what Telegram temporarily
    # allows the bot to message even when the user never started the bot.
    # Do NOT fall back to user_id here for never-started users.
    if not user_chat_id:
        logger.warning(
            "No join-request user_chat_id available for approval message owner=%s user=%s",
            owner, user_id,
        )
        return
    target_chat_id=int(user_chat_id)
    try:
        if not media:
            if text or markup:
                await bot.send_message(chat_id=target_chat_id, text=text or " ", reply_markup=markup)
            return
        for idx,entry in enumerate(media):
            fid=entry.get("file_id")
            typ=entry.get("type")
            caption=text if idx == 0 else None
            if typ=="photo":
                await bot.send_photo(chat_id=target_chat_id, photo=fid, caption=caption, reply_markup=markup if idx==0 else None)
            elif typ=="video":
                await bot.send_video(chat_id=target_chat_id, video=fid, caption=caption, reply_markup=markup if idx==0 else None)
            elif typ=="document":
                await bot.send_document(chat_id=target_chat_id, document=fid, caption=caption, reply_markup=markup if idx==0 else None)
    except Exception:
        logger.exception("Forced Join approval editor message failed owner=%s user=%s", owner, user_id)

async def forced_join_request(update, context):
    req=update.chat_join_request
    if not req:
        return

    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner:
        return
    if not await get_forced_join_enabled(owner):
        return

    access_chat_id=int(req.chat.id)
    user_id=int(req.user_chat_id)

    required=[
        x for x in await list_required(owner)
        if x.get("enabled", True)
        and int(x.get("chat_id", 0) or 0) != access_chat_id
    ]

    if not required:
        try:
            # Send while the join-request private chat is available, then approve.
            # This works even if the user has never pressed /start.
            await _send_forced_join_approval_message(context.bot, owner, user_id, req.user_chat_id)
            await context.bot.approve_chat_join_request(access_chat_id, user_id)
        except Exception:
            logger.exception("Automatic approval failed access=%s user=%s", access_chat_id, user_id)
        return

    # Check all required chats immediately. If the user is already a member
    # everywhere, approve without sending a Forced Join message.
    ok, missing=await _required_status(context.bot, user_id, required)
    if ok:
        try:
            await _send_forced_join_approval_message(context.bot, owner, user_id, req.user_chat_id)
            await context.bot.approve_chat_join_request(access_chat_id, user_id)
            await remove_pending_request(owner, user_id, access_chat_id)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ All required groups/channels are joined.\n"
                        "Your access request has been approved."
                    ),
                )
            except Exception:
                logger.exception("Forced Join approval status message failed owner=%s user=%s", owner, user_id)
        except Exception:
            logger.exception("Automatic approval failed access=%s user=%s", access_chat_id, user_id)
        return

    # Save the original request so later ChatMember updates can approve it
    # automatically after every required chat has been joined.
    await save_pending_request(owner, user_id, access_chat_id, req.user_chat_id)

    rows=[]
    for item in required:
        link=str(item.get("invite_link") or "").strip()
        if not link:
            try:
                invite=await context.bot.create_chat_invite_link(
                    int(item["chat_id"]), name="Forced Join", member_limit=0
                )
                link=invite.invite_link
                await update_invite(owner, int(item["chat_id"]), link)
            except Exception:
                logger.exception(
                    "Could not create Forced Join invite owner=%s chat=%s",
                    owner, item.get("chat_id")
                )

        # Per-item status: already joined = disabled/info button;
        # missing = clickable Join button.
        try:
            member=await context.bot.get_chat_member(int(item["chat_id"]), user_id)
            status=str(getattr(member, "status", "") or "")
            joined=status in {"creator", "administrator", "member"} or (
                status == "restricted" and bool(getattr(member, "is_member", False))
            )
        except Exception:
            joined=False

        title=str(item.get("title") or "Required Group/Channel")[:35]
        if joined:
            rows.append([
                InlineKeyboardButton(f"📎 Joined {title} ✅", callback_data="fj_info:joined")
            ])
        elif link:
            rows.append([
                InlineKeyboardButton(f"📎 Join {title} ❌", url=link)
            ])
        else:
            rows.append([
                InlineKeyboardButton(f"📎 Join {title} ❌", callback_data="fj_info:missing")
            ])

    text=(
        "🔐 Join Required\n\n"
        "To access this private channel, first join the required "
        "group/channel(s) below.\n\n"
        "After all required groups/channels are joined, your original "
        "access request will be approved automatically."
    )

    try:
        # Same target/method as the custom approval message: the temporary
        # join-request private chat. This works even when /start was never used.
        await context.bot.send_message(
            chat_id=int(req.user_chat_id),
            text=text,
            reply_markup=_kb(rows) if rows else None,
        )
    except Exception:
        logger.exception("Forced Join DM failed owner=%s user=%s access=%s", owner, user_id, access_chat_id)

async def forced_join_auto_approve(update, context):
    """Approve pending private-channel requests after required membership changes."""
    cm=update.chat_member
    if not cm:
        return

    new=cm.new_chat_member
    status=str(getattr(new, "status", "") or "")
    is_member=bool(getattr(new, "is_member", False))
    if status in {"left", "kicked"} or (status == "restricted" and not is_member):
        return

    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner or not await get_forced_join_enabled(owner):
        return
    user_id=int(getattr(new.user, "id", 0) or 0)
    if not owner or not user_id:
        return

    required=[
        x for x in await list_required(owner)
        if x.get("enabled", True)
    ]
    if not required:
        return

    pending=await list_pending_requests(owner, user_id)
    if not pending:
        return

    # Check every required chat. This makes approval independent of which
    # required group/channel generated the latest ChatMember update.
    ok, missing=await _required_status(context.bot, user_id, required)
    if not ok:
        return

    for request in pending:
        access_chat_id=int(request.get("access_chat_id", 0) or 0)
        if not access_chat_id:
            continue
        try:
            request_chat_id=int(request.get("user_chat_id", user_id) or user_id)
            await _send_forced_join_approval_message(context.bot, owner, user_id, request_chat_id)
            await context.bot.approve_chat_join_request(access_chat_id, user_id)
            await remove_pending_request(owner, user_id, access_chat_id)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ All required groups/channels are joined.\n\n"
                        "Your private-channel access request has been "
                        "approved automatically."
                    ),
                )
            except Exception:
                logger.exception("Forced Join approval status message failed owner=%s user=%s", owner, user_id)
        except Exception:
            logger.exception(
                "Forced Join automatic approval failed access=%s user=%s",
                access_chat_id, user_id
            )

async def forced_join_info_callback(update, context):
    q=update.callback_query
    if not q:
        return True
    await q.answer()
    a=q.data or ""
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    if a=="fj_forced_groups":
        await forced_join_groups_page(q, context)
        return True
    if a=="fj_toggle_feature":
        enabled=await get_forced_join_enabled(owner)
        await set_forced_join_enabled(owner, not enabled)
        await forced_join_page(q, context)
        return True
    if a.startswith("fj_info:"):
        if a.endswith(":joined"):
            await q.answer("✅ You have already joined this required group/channel.")
        else:
            await q.answer("Please use the Join button for this required group/channel.")
    return True


FORCED_JOIN_APPROVAL_VARIABLES = (
    "{ID} = user ID\n"
    "{NAME} = first name\n"
    "{SURNAME} = surname\n"
    "{NAMESURNAME} = full name\n"
    "{DATE} = current date\n"
    "{TIME} = current time\n"
    "{WEEKDAY} = week day\n"
    "{MENTION} = Link to the user profile\n"
    "{USERNAME} = username"
)


def _approval_buttons_header() -> str:
    return (
        "👉 Set the buttons to be placed under the message\n\n"
        "Send a message structured as follows:\n\n"
        "• Add a single button:\n"
        "Button title - t.me/LinkExample\n\n"
        "• Add multiple buttons on a single line:\n"
        "Button title - t.me/LinkExample && Button text - t.me/LinkExample\n\n"
        "• Add multiple rows of buttons:\n"
        "Button title - t.me/LinkExample\n"
        "Button title - t.me/LinkExample\n\n"
        "⚡ Feature Buttons\n\n"
        "• Add a feature button:\n"
        "Button title - feature: feature_name\n\n"
        "Available feature names:\n"
        "plans, buy, profile, renew, referral, referral_unlock, support, home"
    )


def _parse_approval_buttons(text: str):
    """Approval-message buttons support only URL/@username and feature targets."""
    rows=[]
    for line_no, raw_line in enumerate((text or "").splitlines(), 1):
        raw_line=raw_line.strip()
        if not raw_line:
            continue
        row=[]
        for button_no, item in enumerate(raw_line.split("&&"), 1):
            item=item.strip()
            if " - " not in item:
                raise ValueError(f"Line {line_no}, button {button_no}: missing ' - '. Example: Button title - t.me/LinkExample")
            title,target=[part.strip() for part in item.split(" - ",1)]
            if not title or not target:
                raise ValueError(f"Line {line_no}, button {button_no}: button title and target are required.")
            if target.startswith("feature:"):
                feature=target.split(":",1)[1].strip().lower()
                callback=FEATURE_CALLBACKS.get(feature)
                if not callback:
                    raise ValueError(f"Line {line_no}, button {button_no}: unknown feature '{feature}'. Available: {', '.join(FEATURE_CALLBACKS)}")
                row.append({"text":title,"type":"callback","value":callback,"target":f"feature: {feature}"})
                continue
            if target.startswith("t.me/"):
                target="https://"+target
            elif target.startswith("http://") or target.startswith("https://"):
                pass
            elif target.startswith("@"):
                target="https://t.me/"+target[1:]
            else:
                raise ValueError(
                    f"Line {line_no}, button {button_no}: only URL/@username or feature:<name> is supported."
                )
            row.append({"text":title,"type":"url","value":target,"target":target})
        rows.append(row)
    if not rows:
        raise ValueError("No buttons found. Add at least one button.")
    return rows


def _approval_markup(rows):
    """Build only URL/feature buttons; old special-button types are ignored."""
    clean=[]
    for row in rows or []:
        clean_row=[]
        for item in row:
            kind=item.get("type")
            if kind in {"url","callback"} and item.get("value"):
                clean_row.append(item)
        if clean_row:
            clean.append(clean_row)
    return build_editor_keyboard(clean)


def _approval_button_lines(rows):
    lines=[]
    for row in rows or []:
        parts=[]
        for item in row:
            text=str(item.get("text") or "Button")
            target=str(item.get("target") or "")
            if not target:
                if item.get("type")=="callback":
                    target="feature: " + next((k for k,v in FEATURE_CALLBACKS.items() if v==item.get("value")), "home")
                else:
                    target=str(item.get("value") or "")
            if target.startswith("https://t.me/"):
                target=target[len("https://"):]
            parts.append(f"{text} - {target}")
        if parts:
            lines.append(" && ".join(parts))
    return "\n".join(lines) or "❌ No buttons configured."

async def forced_join_message_editor(q, context):
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    item=await get_forced_join_editor(owner)
    enabled=await get_forced_join_editor_enabled(owner)
    # Editor page has no target user. Keep configured variables untouched here;
    # they are resolved only when the approval message is actually delivered.
    text=item.get("text") or ""
    media=item.get("media") or []
    buttons=item.get("buttons") or []
    button_count=sum(1 for row in buttons for b in row if b.get("type") in {"url","callback"})
    rows=[
        [InlineKeyboardButton(("🟢 Disable" if enabled else "🔴 Enable") + " Approval Message", callback_data="fj_editor_toggle")],
        [InlineKeyboardButton("📝 Text",callback_data="fj_editor_text"), InlineKeyboardButton("👀 See",callback_data="fj_editor_text_see")],
        [InlineKeyboardButton("🖼 Media",callback_data="fj_editor_media"), InlineKeyboardButton("👀 See",callback_data="fj_editor_media_see")],
        [InlineKeyboardButton("🔗 Buttons",callback_data="fj_editor_buttons"), InlineKeyboardButton("👀 See",callback_data="fj_editor_buttons_see")],
        [InlineKeyboardButton("👀 Full Preview",callback_data="fj_editor_preview")],
        [InlineKeyboardButton("⬅ Back",callback_data="gm_forced_join")],
    ]
    media_line = f"🖼 Media: {len(media)}/10" if media else "🖼 Media: ❌ Not added"
    await q.edit_message_text(
        "📝 Forced Join Approval Message\n\n"
        "Sent after all required groups/channels are joined and the original access request is approved.\n\n"
        "Current Setup\n\n"
        f"Status: {'🟢 Enabled' if enabled else '🔴 Disabled'}\n"
        f"📝 Text: {'✅ Added' if text else '❌ Not added'}\n"
        f"{media_line}\n"
        f"🔗 Buttons: {button_count}",
        reply_markup=_kb(rows),
    )


async def forced_join_editor_callback(update, context):
    q=update.callback_query
    await q.answer()
    a=q.data or ""
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    item=await get_forced_join_editor(owner)

    if a=="fj_editor":
        await forced_join_message_editor(q,context); return True
    if a=="fj_editor_toggle":
        current=await get_forced_join_editor_enabled(owner)
        enabled=await set_forced_join_editor_enabled(owner, not current)
        await forced_join_message_editor(q,context)
        await q.answer("✅ Approval Message enabled." if enabled else "⛔ Approval Message disabled.", show_alert=True)
        return True

    if a=="fj_editor_text":
        context.user_data["fj_editor_input"]="text"
        await q.edit_message_text(
            "📝 Forced Join Approval Message\n\n"
            "Send the message you want to set.\n\n"
            "You can use HTML and:\n"
            + FORCED_JOIN_APPROVAL_VARIABLES,
            reply_markup=_kb([[InlineKeyboardButton("⬅ Back",callback_data="fj_editor")]]),
        )
        return True

    if a=="fj_editor_text_see":
        text=item.get("text") or "❌ No text added."
        await q.edit_message_text(
            "📝 Current Text\n\n" + text,
            reply_markup=_kb([[InlineKeyboardButton("⬅ Back",callback_data="fj_editor")]]),
        )
        return True

    if a=="fj_editor_media":
        context.user_data["fj_editor_input"]="media"
        await q.edit_message_text(
            editor_media_prompt("Forced Join Approval Message"),
            reply_markup=_kb([
                [InlineKeyboardButton("🗑 Delete Media",callback_data="fj_editor_media_delete")],
                [InlineKeyboardButton("⬅ Back",callback_data="fj_editor")],
            ]),
        )
        return True

    if a=="fj_editor_media_see":
        media=item.get("media") or []
        if not media:
            await q.answer("❌ No media configured.",show_alert=True)
            return True
        # See = media only. Do not attach approval text or configured buttons.
        e=media[0]; typ=e.get("type"); fid=e.get("file_id")
        if typ=="photo":
            await q.message.reply_photo(fid)
        elif typ=="video":
            await q.message.reply_video(fid)
        else:
            await q.message.reply_document(fid)
        return True

    if a=="fj_editor_media_delete":
        item["media"]=[]
        await set_forced_join_editor(owner,item)
        await q.answer("🗑 Media deleted.")
        await forced_join_message_editor(q,context)
        return True

    if a=="fj_editor_buttons":
        context.user_data["fj_editor_input"]="buttons"
        await q.edit_message_text(
            "🔗 Forced Join Approval Message Buttons\n\n" + _approval_buttons_header(),
            reply_markup=_kb([[InlineKeyboardButton("⬅ Back",callback_data="fj_editor")]]),
        )
        return True

    if a=="fj_editor_buttons_see":
        # See = one message only: exact configured button definitions in the
        # header, followed by the actual configured keyboard preview.
        rows=_approval_button_lines(item.get("buttons") or [])
        text="🔗 Current Buttons\n\n" + rows
        markup=_approval_markup(item.get("buttons") or [])
        if markup:
            markup.append([InlineKeyboardButton("⬅ Back",callback_data="fj_editor")])
        else:
            markup=_kb([[InlineKeyboardButton("⬅ Back",callback_data="fj_editor")]])
        await q.edit_message_text(text,reply_markup=markup)
        return True

    if a=="fj_editor_preview":
        markup=_approval_markup(item.get("buttons") or [])
        text=item.get("text") or "❌ No text added."
        media=item.get("media") or []
        if not media:
            await q.message.reply_text(text,reply_markup=markup)
        else:
            e=media[0]; typ=e.get("type"); fid=e.get("file_id")
            if typ=="photo": await q.message.reply_photo(fid,caption=text,reply_markup=markup)
            elif typ=="video": await q.message.reply_video(fid,caption=text,reply_markup=markup)
            else: await q.message.reply_document(fid,caption=text,reply_markup=markup)
        return True

    if a=="fj_editor_media_back" or a=="fj_editor_buttons_back":
        await forced_join_message_editor(q,context); return True
    return False


async def forced_join_editor_text_input(update, context):
    mode=context.user_data.get("fj_editor_input")
    if mode not in {"text","buttons"}:
        return False
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    item=await get_forced_join_editor(owner)
    text=(update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("❌ Cannot be empty.")
        return True
    if mode=="text":
        item["text"]=text
    else:
        try:
            item["buttons"]=_parse_approval_buttons(text)
        except ValueError as e:
            await update.effective_message.reply_text(f"❌ {e}")
            return True
    await set_forced_join_editor(owner,item)
    context.user_data.pop("fj_editor_input",None)
    await update.effective_message.reply_text(
        "✅ Saved.",
        reply_markup=_kb([[InlineKeyboardButton("⬅ Continue",callback_data="fj_editor")]]),
    )
    return True

async def forced_join_editor_media_input(update, context):
    if context.user_data.get("fj_editor_input")!="media":
        return False
    m=update.effective_message
    entry=None
    if m.photo: entry={"type":"photo","file_id":m.photo[-1].file_id}
    elif m.video: entry={"type":"video","file_id":m.video.file_id}
    elif m.document: entry={"type":"document","file_id":m.document.file_id}
    if not entry:
        return False
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    item=await get_forced_join_editor(owner)
    media=item.get("media") or []
    media=[entry]  # replace current approval media
    item["media"]=media
    await set_forced_join_editor(owner,item)
    context.user_data.pop("fj_editor_input",None)
    await m.reply_text(
        "✅ Media saved.",
        reply_markup=_kb([[InlineKeyboardButton("⬅ Continue",callback_data="fj_editor")]]),
    )
    return True

async def forced_join_page(q, context):
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    enabled=await get_forced_join_enabled(owner)
    rows=[
        [InlineKeyboardButton(("🔴 Disable Forced Join" if enabled else "🟢 Enable Forced Join"), callback_data="fj_toggle_feature")],
        [InlineKeyboardButton("🔗 Forced Group/Channel",callback_data="fj_forced_groups")],
        [InlineKeyboardButton("📝 Approval Message",callback_data="fj_editor")],
        [InlineKeyboardButton("⬅ Back",callback_data="gm_group")],
    ]
    await q.edit_message_text(
        "🔗 Forced Join\n\n"
        f"Status: {'🟢 Enabled' if enabled else '🔴 Disabled'}\n\n"
        "Manage the groups/channels used for Forced Join and the "
        "message sent after automatic approval.",
        reply_markup=_kb(rows)
    )

async def forced_join_groups_page(q, context):
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    items=await list_required(owner)
    rows=[]
    for x in items:
        mark="🟢" if x.get("enabled",True) else "🔴"
        rows.append([InlineKeyboardButton(
            f"{mark} {str(x.get('title') or 'Group/Channel')[:32]}",
            callback_data=f"fj_toggle:{int(x['chat_id'])}"
        )])
    rows.append([InlineKeyboardButton("⬅ Back",callback_data="gm_forced_join")])
    await q.edit_message_text(
        "🔗 Forced Group/Channel\n\n"
        "Groups/channels added with /connectforcedjoin are shown here.\n\n"
        "How to connect:\n"
        "/connectforcedjoin <chat_id>\n"
        "or /connectforcedjoin @username",
        reply_markup=_kb(rows)
    )


async def forced_join_toggle_callback(update, context):
    q=update.callback_query
    await q.answer()
    try:
        chat_id=int((q.data or "").split(":",1)[1])
    except Exception:
        return
    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    await toggle_required(owner,chat_id)
    await forced_join_page(q,context)
    return True

async def connect_forced_join_command(self, update, context):
    owner=self.owner(context)
    if not await self.auth(update,context):
        return
    message=update.effective_message
    chat=update.effective_chat
    target_id=chat.id if chat and chat.type in {"group","supergroup","channel"} else 0
    if context.args:
        raw_target=str(context.args[0]).strip()
        try:
            target_id=int(raw_target)
        except ValueError:
            try:
                info=await context.bot.get_chat(raw_target)
                target_id=int(info.id)
            except Exception:
                await message.reply_text("❌ Send a valid chat ID or @username.")
                return
    if not target_id:
        await message.reply_text(
            "❌ Use this command inside the required group/channel, "
            "or send /connectforcedjoin <chat_id> (or @username) from the bot admin chat."
        )
        return
    try:
        info=await context.bot.get_chat(target_id)

        member=await context.bot.get_chat_member(target_id, context.bot.id)
        if getattr(member,"status","") not in {"administrator","creator"}:
            await message.reply_text("❌ Bot must be an administrator in this group/channel.")
            return
        if getattr(member,"status","") != "creator" and not getattr(member,"can_invite_users",False):
            await message.reply_text(
                "❌ Bot needs the Invite Users permission in this group/channel."
            )
            return
        invite=await context.bot.create_chat_invite_link(
            target_id,name="Forced Join",member_limit=0
        )
        from database.forced_join import upsert_required
        await upsert_required(owner,target_id,info.title or "Group/Channel",info.type,invite.invite_link)
        await message.reply_text(
            f"✅ Forced Join group/channel connected.\n\n"
            f"Name: {info.title or 'Group/Channel'}\n"
            f"ID: {target_id}\n\n"
            "It is now available in Group Manager → Forced Join."
        )
    except Exception as exc:
        logger.exception("connectforcedjoin failed")
        await message.reply_text(f"❌ Could not connect this group/channel.\n\n{exc}")
