"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import register_feature_origin


class CloneSupportCoreMixin:
    _support_topic_locks = {}

    @staticmethod
    def _support_datetime(value, timezone_name="Asia/Kolkata", fmt="%d-%m-%Y %I:%M:%S %p %Z"):
        """Format support dates without depending on SellerBotManager."""
        if not value:
            return "-"
        try:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            try:
                zone = ZoneInfo(timezone_name or "Asia/Kolkata")
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                zone = ZoneInfo("Asia/Kolkata")
            return value.astimezone(zone).strftime(fmt)
        except Exception:
            return str(value)

    async def support_user_details_text(self,owner,user):
        # Accept either a Telegram User object or a raw Telegram user ID.
        # Live Support navigation passes the ID from callback_data.
        if isinstance(user, int):
            user_id = int(user)
            record = await get_user(owner, user_id) or {}

            class _SupportUser:
                id = user_id
                first_name = record.get("first_name") or ""
                last_name = record.get("last_name") or ""
                username = record.get("username")
                language_code = record.get("language_code")

                @property
                def full_name(self):
                    return " ".join(
                        value for value in (self.first_name, self.last_name) if value
                    ) or str(self.id)

            user = _SupportUser()
        else:
            record=await get_user(owner,user.id) or {}
        sub=await get_subscription(owner,user.id) or {}
        expiry=sub.get("expiry_date")
        if expiry and expiry.tzinfo is None:
            expiry=expiry.replace(tzinfo=timezone.utc)
        now=datetime.now(timezone.utc)
        active=bool(sub.get("active") and expiry and expiry>now)
        remaining="-"
        if active and expiry:
            seconds=max(0,int((expiry-now).total_seconds()))
            days,rem=divmod(seconds,86400)
            hours,rem=divmod(rem,3600)
            minutes=rem//60
            parts=[]
            if days: parts.append(f"{days}d")
            if hours: parts.append(f"{hours}h")
            if minutes or not parts: parts.append(f"{minutes}m")
            remaining=" ".join(parts)
        full_name=html.escape(user.full_name or str(user.id))
        username=("@"+html.escape(user.username)) if user.username else "Not Set"
        mention=f'<a href="tg://user?id={user.id}">{full_name}</a>'
        joined=record.get("joined_at") or record.get("created_at")
        plan=sub.get("plan") or sub.get("plan_name") or "No Plan"
        return (
            "👤 <b>Live Support User Details</b>\n\n"
            f"• Name: {full_name}\n"
            f"• Username: {username}\n"
            f"• Mention: {mention}\n"
            f"• User ID: <code>{user.id}</code>\n"
            f"• Language: {html.escape(user.language_code or record.get('language_code') or 'Unknown')}\n"
            f"• Joined: {self._support_datetime(joined)}\n\n"
            "📦 <b>Subscription Details</b>\n\n"
            f"• Status: {'✅ Active' if active else '❌ Inactive'}\n"
            f"• Plan: {html.escape(str(plan))}\n"
            f"• Expiry: {self._support_datetime(expiry)}\n"
            f"• Remaining: {remaining}\n\n"
            "All future messages from this user will stay in this permanent topic."
        )

    @staticmethod
    def support_topic_keyboard(user_id,blocked=False):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Open Telegram Profile",url=f"tg://user?id={int(user_id)}")],
            [InlineKeyboardButton("📋 View User Details",callback_data=f"support_profile_{int(user_id)}")],
            [InlineKeyboardButton("🎁 Give / Extend Subscription",callback_data=f"support_extend_{int(user_id)}")],
            [InlineKeyboardButton(
                "✅ Unblock Support" if blocked else "🚫 Block Support",
                callback_data=(f"support_unblock_{int(user_id)}" if blocked else f"support_block_{int(user_id)}"),
            )],
            [InlineKeyboardButton("🆔 Show User ID",callback_data=f"support_id_{int(user_id)}")],
        ])

    async def _send_support_header_reliably(self, context, **kwargs):
        """Send the topic header only after Telegram has made the new thread usable."""
        last_error=None
        for attempt in range(8):
            try:
                return await context.bot.send_message(**kwargs)
            except (TimedOut, NetworkError, RetryAfter) as exc:
                last_error=exc
                delay=float(getattr(exc,"retry_after",0) or (0.6*(attempt+1)))
                await asyncio.sleep(min(max(delay,0.4),6.0))
            except BadRequest as exc:
                text=str(exc or "").lower()
                # Telegram can return the forum topic before the thread accepts
                # messages. Treat that as temporary instead of abandoning the
                # first customer message.
                if any(marker in text for marker in (
                    "message thread not found", "topic_closed", "topic closed",
                    "message thread is closed", "forum topic",
                )):
                    last_error=exc
                    await asyncio.sleep(min(0.75 + (attempt * 0.5), 5.0))
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Could not send support user details")

    async def _send_support_user_header(self, context, owner, user, topic):
        """Best-effort topic header that can never fail customer delivery."""
        if topic.get("header_sent"):
            return topic
        if not await claim_support_topic_header(owner, user.id):
            return await get_support_topic(owner, user.id) or topic

        try:
            group_id = int(topic["support_group_id"])
            thread_id = int(topic["message_thread_id"])
            blocked = await is_support_blocked(owner, user.id)
            details = await self.support_user_details_text(owner, user)
            try:
                sent = await self._send_support_header_reliably(
                    context,
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=details,
                    parse_mode="HTML",
                    reply_markup=self.support_topic_keyboard(user.id, blocked),
                    disable_web_page_preview=True,
                )
            except BadRequest:
                logger.exception(
                    "Support details HTML failed; sending plain text owner=%s user=%s",
                    owner, user.id,
                )
                sent = await self._send_support_header_reliably(
                    context,
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=(
                        f"🆕 New Support User\n\n"
                        f"👤 Name: {user.full_name or user.id}\n"
                        f"📝 Username: @{user.username if user.username else 'Not set'}\n"
                        f"🆔 User ID: {user.id}\n"
                        f"🔗 Mention: tg://user?id={user.id}"
                    ),
                    reply_markup=self.support_topic_keyboard(user.id, blocked),
                    disable_web_page_preview=True,
                )
            await mark_support_topic_header(owner, user.id, sent.message_id)
        except Exception as exc:
            # The header is informational. Never let it abort or consume the
            # customer's original support message.
            await release_support_topic_header_claim(owner, user.id)
            logger.warning(
                "Support user header failed but message delivery will continue "
                "owner=%s user=%s error=%s", owner, user.id, exc, exc_info=True,
            )
        return await get_support_topic(owner, user.id) or topic

    async def ensure_support_topic(self,context,owner,user,support):
        """Return exactly one permanent topic for a user.

        Uses both an in-process lock and a MongoDB creation lease, so duplicate
        topics cannot be created by concurrent updates or multiple Render
        workers. The user-details message is also guaranteed once per topic.
        """
        group_id=int(support["support_group_id"])
        lock_key=(int(owner),int(user.id),group_id)
        lock=self._support_topic_locks.setdefault(lock_key,asyncio.Lock())
        async with lock:
            topic=await get_support_topic(owner,user.id)
            if (
                topic
                and int(topic.get("support_group_id",0))==group_id
                and topic.get("message_thread_id")
                and topic.get("status") != "failed"
            ):
                return await self._send_support_user_header(
                    context, owner, user, topic,
                )

            claim_token, topic = await claim_support_topic_creation(
                owner, user.id, group_id,
            )
            if not claim_token:
                # Another process is creating it. Wait briefly for completion
                # instead of creating a second Telegram forum topic.
                for _ in range(60):
                    await asyncio.sleep(0.25)
                    topic=await get_support_topic(owner,user.id)
                    if (
                        topic
                        and int(topic.get("support_group_id",0))==group_id
                        and topic.get("message_thread_id")
                        and topic.get("status") != "failed"
                    ):
                        return await self._send_support_user_header(
                            context, owner, user, topic,
                        )
                raise RuntimeError("Support topic creation is still in progress")

            forum_topic=None
            try:
                topic_name=f"👤 {user.first_name or 'User'} | {user.id}"[:128]
                forum_topic=await context.bot.create_forum_topic(
                    group_id,name=topic_name,
                )
                # Publish the mapping first. Header delivery is optional and
                # must never prevent the first customer message from using the
                # newly-created topic.
                await asyncio.sleep(0.8)
                provisional={
                    "owner_id": int(owner),
                    "user_id": int(user.id),
                    "support_group_id":group_id,
                    "message_thread_id":forum_topic.message_thread_id,
                    "topic_name":topic_name,
                    "status":"ready",
                    "header_sent":False,
                }
                topic=await complete_support_topic_creation(
                    owner,user.id,claim_token,group_id,
                    forum_topic.message_thread_id,topic_name,None,
                )
                topic = topic or provisional
                return await self._send_support_user_header(
                    context, owner, user, topic,
                )
            except Exception:
                await fail_support_topic_creation(owner,user.id,claim_token)
                # Remove an empty orphan topic when creation failed after the
                # Telegram API already created it.
                if forum_topic is not None:
                    try:
                        await context.bot.delete_forum_topic(
                            chat_id=group_id,
                            message_thread_id=forum_topic.message_thread_id,
                        )
                    except Exception:
                        logger.warning(
                            "Could not remove orphan support topic owner=%s user=%s",
                            owner,user.id,
                        )
                raise

    async def support_template_values(self,owner,user):
        sub=await get_subscription(owner,user.id) or {}
        expiry=sub.get("expiry_date")
        values={
            "{NAME}":user.full_name or str(user.id),
            "{ID}":str(user.id),
            "{USERNAME}":("@"+user.username) if user.username else "",
            "{PLAN}":str(sub.get("plan") or "No Plan"),
            "{EXPIRY}":self._support_datetime(expiry),
        }
        return values

    async def send_support_template(self,context,owner,target_user_id,template,user_obj=None):
        if not template:
            raise ValueError("Template not found")
        if user_obj is None:
            record=await get_user(owner,target_user_id) or {}
            class UserView:
                id=int(target_user_id)
                full_name=" ".join(x for x in [record.get("first_name"),record.get("last_name")] if x) or str(target_user_id)
                username=record.get("username")
            user_obj=UserView()
        text=template.get("text") or ""
        for key,value in (await self.support_template_values(owner,user_obj)).items():
            text=text.replace(key,value)
        keyboard=self.build_welcome_keyboard(template.get("buttons") or [])
        file_id=template.get("media_file_id")
        media_type=template.get("media_type")
        kwargs={"chat_id":int(target_user_id),"reply_markup":keyboard}
        if file_id and media_type=="photo": sent=await context.bot.send_photo(photo=file_id,caption=text or None,**kwargs)
        elif file_id and media_type=="video": sent=await context.bot.send_video(video=file_id,caption=text or None,**kwargs)
        elif file_id and media_type=="animation": sent=await context.bot.send_animation(animation=file_id,caption=text or None,**kwargs)
        elif file_id and media_type=="document": sent=await context.bot.send_document(document=file_id,caption=text or None,**kwargs)
        else: sent=await context.bot.send_message(text=text or "(Empty template)",disable_web_page_preview=True,**kwargs)
        register_feature_origin(sent, text=text or "(Empty template)", markup=keyboard)
        auto_delete_seconds=_template_auto_delete_seconds(template)
        if auto_delete_seconds > 0:
            asyncio.create_task(self._delete_template_message_later(context.bot,sent.chat_id,sent.message_id,auto_delete_seconds))
        return sent

    @staticmethod
    async def _delete_template_message_later(bot,chat_id,message_id,delay_seconds):
        try:
            await asyncio.sleep(max(1,int(delay_seconds)))
            await bot.delete_message(chat_id=chat_id,message_id=message_id)
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            logger.warning("Template auto-remove failed chat=%s message=%s: %s",chat_id,message_id,exc)
        except Exception:
            logger.exception("Unexpected template auto-remove failure chat=%s message=%s",chat_id,message_id)

