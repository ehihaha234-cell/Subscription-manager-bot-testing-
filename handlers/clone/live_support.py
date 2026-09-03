"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from handlers.clone.admin.live_support import _live_support_parse_buttons


# One FIFO lock per seller/user pair. Telegram can dispatch several updates
# concurrently; without this lock rapid customer messages can overtake each
# other while the first support topic is being created.
_LIVE_SUPPORT_FIFO_LOCKS = {}


def _live_support_fifo_lock(owner_id: int, user_id: int):
    key = (int(owner_id), int(user_id))
    lock = _LIVE_SUPPORT_FIFO_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LIVE_SUPPORT_FIFO_LOCKS[key] = lock
    return lock


class CloneLiveSupportMixin:
    @staticmethod
    def _is_stale_support_topic_error(exc):
        text = str(exc or "").lower()
        markers = (
            "message thread not found",
            "topic_closed",
            "topic closed",
            "message thread is closed",
            "forum topic",
        )
        return any(marker in text for marker in markers)

    async def _copy_to_support_topic_reliably(self, context, topic, from_chat_id, message_id):
        """Deliver every customer message with retries and a forward fallback.

        New Telegram forum topics can briefly reject the first copy even after
        create_forum_topic succeeds. We keep retrying the exact same source
        message id, so text, links and media are never silently skipped.
        """
        last_error = None
        for attempt in range(24):
            try:
                return await context.bot.copy_message(
                    chat_id=int(topic["support_group_id"]),
                    message_thread_id=int(topic["message_thread_id"]),
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
            except (TimedOut, NetworkError, RetryAfter) as exc:
                last_error = exc
                delay = float(getattr(exc, "retry_after", 0) or (0.55 * (attempt + 1)))
                await asyncio.sleep(min(max(delay, 0.35), 6.0))
            except BadRequest as exc:
                last_error = exc
                text = str(exc or "").lower()
                if self._is_stale_support_topic_error(exc):
                    # A freshly-created topic can temporarily report the same
                    # error as a missing thread. Retry before declaring it stale.
                    await asyncio.sleep(min(0.8 + (attempt * 0.45), 5.0))
                    continue
                # Some Telegram message types cannot be copied. Forwarding keeps
                # the original text/link/media intact.
                try:
                    return await context.bot.forward_message(
                        chat_id=int(topic["support_group_id"]),
                        message_thread_id=int(topic["message_thread_id"]),
                        from_chat_id=from_chat_id,
                        message_id=message_id,
                    )
                except (TimedOut, NetworkError, RetryAfter) as forward_exc:
                    last_error = forward_exc
                    await asyncio.sleep(min(0.7 + (attempt * 0.45), 6.0))
                except TelegramError:
                    raise exc
            except TelegramError as exc:
                # Final format fallback for copy-restricted messages.
                try:
                    return await context.bot.forward_message(
                        chat_id=int(topic["support_group_id"]),
                        message_thread_id=int(topic["message_thread_id"]),
                        from_chat_id=from_chat_id,
                        message_id=message_id,
                    )
                except TelegramError:
                    raise exc
        if last_error:
            raise last_error
        raise RuntimeError("Support message delivery exhausted all retries")

    async def _ensure_support_topic_reliably(self, context, owner, user, support):
        """Wait for/create the permanent topic before forwarding the first message."""
        last_error=None
        for attempt in range(6):
            try:
                return await self.ensure_support_topic(context, owner, user, support)
            except (TimedOut, NetworkError, RetryAfter, RuntimeError) as exc:
                last_error=exc
                delay=float(getattr(exc,"retry_after",0) or (0.9*(attempt+1)))
                await asyncio.sleep(min(max(delay,0.5),4.0))
        if last_error:
            raise last_error
        raise RuntimeError("Support topic could not be prepared")

    async def route_live_support_message(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        message=update.effective_message
        user=update.effective_user
        chat=update.effective_chat
        if not message or not user or user.is_bot or not chat:
            return
        # Telegram Business-account updates belong exclusively to Business
        # Automation and must never enter Clone Bot Live Support.  Telegram can
        # deliver both new and edited business messages, while
        # ``effective_message`` may still expose the business message object.
        # Check every update variant as well as the message-level connection ID
        # so text, media, albums and edited messages are all excluded.
        is_business_update = bool(
            getattr(update, "business_message", None) is not None
            or getattr(update, "edited_business_message", None) is not None
            or getattr(update, "business_connection", None) is not None
            or getattr(update, "deleted_business_messages", None) is not None
            or getattr(message, "business_connection_id", None)
        )
        if is_business_update:
            return
        owner=self.owner(context)
        # Database scope is clone-specific; seller identity is the real Telegram account.
        seller_account=self.seller_account(context)
        support=await get_live_support_settings(owner)

        # Seller reply inside the connected topic group.
        # Restored working direct-delivery flow: no receipt claim/queue layer.
        if (
            support.get("enabled") and support.get("mode")=="topic"
            and support.get("support_group_id")
            and int(chat.id)==int(support["support_group_id"])
            and message.message_thread_id
        ):
            if user.id!=seller_account:
                return
            topic=await get_topic_by_thread(owner,chat.id,message.message_thread_id)
            if not topic:
                return
            try:
                await context.bot.copy_message(
                    chat_id=int(topic["user_id"]),
                    from_chat_id=chat.id,
                    message_id=message.message_id,
                )
            except TelegramError as exc:
                logger.warning("Support topic reply failed owner=%s user=%s: %s",owner,topic.get("user_id"),exc)
            raise ApplicationHandlerStop

        # Seller reply in normal private mode must be a reply to a copied user message.
        if chat.type=="private" and user.id==seller_account:
            if support.get("enabled") and support.get("mode")=="private" and message.reply_to_message:
                link=await get_private_message_link(owner,chat.id,message.reply_to_message.message_id)
                if link:
                    await context.bot.copy_message(
                        chat_id=int(link["user_id"]),
                        from_chat_id=chat.id,
                        message_id=message.message_id,
                    )
                    raise ApplicationHandlerStop
            return

        # Users send an actual non-command content message in private chat.
        if chat.type!="private" or user.id==seller_account:
            return
        has_user_content=bool(
            message.text
            or message.caption
            or message.effective_attachment
            or message.contact
            or message.location
            or message.venue
            or message.poll
        )
        if not has_user_content or not support.get("enabled"):
            return
        special_states={
            "waiting_child_screenshot","wait_qr","wait_welcome_media","wait_broadcast",
            "wait_scheduled_broadcast","wait_channel","wait_plan_add","wait_plan_edit",
            "ba_editor","ba_auth","ba_media_batch",
        }
        if any(context.user_data.get(key) for key in special_states):
            return
        if await is_support_blocked(owner,user.id):
            await message.reply_text("🚫 You cannot contact live support right now.")
            raise ApplicationHandlerStop

        await upsert_user(owner,user)
        auto_reply=None
        if message.text and not message.text.startswith("/"):
            auto_reply=await match_support_auto_reply(owner,message.text)
        mode=support.get("mode","topic")

        # Direct FIFO delivery: the same original Telegram message ID is retried
        # until Telegram accepts it. No receipt queue, debounce or background
        # forwarding is involved.
        try:
            async with _live_support_fifo_lock(owner, user.id):
                if mode=="topic":
                    if not support.get("support_group_id"):
                        await message.reply_text("⚠️ Live support group is not connected yet. Please try again later.")
                        raise ApplicationHandlerStop

                    topic=await self._ensure_support_topic_reliably(context,owner,user,support)
                    try:
                        await self._copy_to_support_topic_reliably(
                            context, topic, chat.id, message.message_id,
                        )
                    except BadRequest as exc:
                        if not self._is_stale_support_topic_error(exc):
                            raise
                        logger.warning("Support topic stale owner=%s user=%s: %s",owner,user.id,exc)
                        await reset_support_topic_mapping(owner,user.id,str(exc))
                        topic=await self._ensure_support_topic_reliably(context,owner,user,support)
                        await self._copy_to_support_topic_reliably(
                            context, topic, chat.id, message.message_id,
                        )
                else:
                    # Telegram delivery must target the actual seller account.
                    # `owner` is only the clone-specific database scope and may
                    # be a composite ID for Clone 2/3/N.
                    await context.bot.send_message(
                        seller_account,
                        f"💬 Live Support\nUser: {user.full_name}\nID: {user.id}\nReply to the copied message below.",
                    )
                    copied=await context.bot.copy_message(
                        chat_id=seller_account,
                        from_chat_id=chat.id,
                        message_id=message.message_id,
                    )
                    # Keep the mapping clone-scoped, but store the real seller
                    # chat ID so replies can be resolved for every clone.
                    await save_private_message_link(owner,seller_account,copied.message_id,user.id)

                if auto_reply:
                    try:
                        await self.send_support_template(context,owner,user.id,auto_reply,user)
                    except TelegramError as exc:
                        logger.warning("Support auto reply failed owner=%s user=%s: %s",owner,user.id,exc)

                confirmation=await message.reply_text("✅ Message sent to live support.")
                async def _delete_support_confirmation():
                    await asyncio.sleep(3)
                    try:
                        await confirmation.delete()
                    except TelegramError:
                        pass
                asyncio.create_task(_delete_support_confirmation())
        except ApplicationHandlerStop:
            raise
        except TelegramError as exc:
            logger.exception("Live support routing failed owner=%s user=%s",owner,user.id)
            await message.reply_text("❌ Message could not be sent to live support. Please try again.")
        except Exception:
            logger.exception("Unexpected live support routing failure owner=%s user=%s",owner,user.id)
            await message.reply_text("❌ Message could not be sent to live support. Please try again.")

        return

    async def support_callback(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        q=update.callback_query
        await q.answer()
        owner=self.owner(context)
        if q.from_user.id!=self.seller_account(context):
            await q.answer("Not authorized",show_alert=True)
            return
        data=q.data
        try:
            user_id=int(data.rsplit("_",1)[-1])
        except ValueError:
            return
        if data.startswith("support_id_"):
            await q.answer(f"User ID: {user_id}",show_alert=True); return
        if data.startswith("support_block_"):
            await set_support_block(owner,user_id,True)
            await q.edit_message_reply_markup(self.support_topic_keyboard(user_id,True)); return
        if data.startswith("support_unblock_"):
            await set_support_block(owner,user_id,False)
            await q.edit_message_reply_markup(self.support_topic_keyboard(user_id,False)); return
        if data.startswith("support_profile_"):
            text,record,sub=await self.user_details_text(owner,user_id)
            if not text:
                await q.answer("User not found",show_alert=True); return
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                message_thread_id=q.message.message_thread_id,
                text=text,
            )
            return

    async def text_handler(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context); text=update.effective_message.text.strip()
        staff = await self.staff_record(update, context)
        if staff:
            if context.user_data.get("wait_pg_webhook_secret"):
                try:
                    if not text:
                        raise ValueError("Webhook Secret cannot be empty")
                    await save_gateway_config("seller",owner,"razorpay",{"webhook_secret":text,"mode":"live"})
                    context.user_data.clear()
                    cfg=await get_gateway_config("seller",owner,decrypt=True); g=(cfg.get("gateways") or {}).get("razorpay",{})
                    await update.effective_message.reply_text(
                        "✅ Webhook Secret saved securely.",
                        reply_markup=_seller_razorpay_keyboard(bool(g.get("enabled"))),
                    )
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ {exc}")
                return
            gateway=context.user_data.get("wait_pg_credentials")
            if gateway:
                values=[x.strip() for x in text.split("|")]
                try:
                    if gateway=="razorpay" and len(values)==2:
                        payload={"key_id":values[0],"key_secret":values[1],"mode":"live"}
                    elif gateway=="cashfree" and len(values)==2:
                        payload={"client_id":values[0],"client_secret":values[1]}
                    elif gateway=="phonepe" and len(values)==5:
                        payload={"client_id":values[0],"client_version":values[1],"client_secret":values[2],"webhook_username":values[3],"webhook_password":values[4]}
                    elif gateway=="paytm" and len(values)==3:
                        payload={"mid":values[0],"merchant_key":values[1],"website_name":values[2]}
                    else:
                        raise ValueError("Invalid credential format")
                    await save_gateway_config("seller",owner,gateway,payload)
                    context.user_data.clear()
                    await update.effective_message.reply_text("✅ Gateway credentials saved securely.",reply_markup=self.back(f"a_pg_view_{gateway}"))
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ {exc}")
                return
            if context.user_data.get("wait_support_ar_keyword"):
                keyword=" ".join(text.strip().lower().split())
                try:
                    await save_support_auto_reply(owner,keyword)
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ {exc}"); return
                context.user_data.clear()
                await update.effective_message.reply_text(
                    "✅ Auto reply created",
                    reply_markup=self.support_auto_reply_edit_menu(keyword),
                ); return
            if context.user_data.get("wait_support_ar_keyword_edit"):
                old_keyword=context.user_data["wait_support_ar_keyword_edit"]
                new_keyword=" ".join(text.strip().lower().split())
                try:
                    item=await get_support_auto_reply(owner,old_keyword)
                    if not item: raise ValueError("Auto reply not found")
                    if not new_keyword: raise ValueError("Keyword cannot be empty")
                    existing=await get_support_auto_reply(owner,new_keyword)
                    if existing and new_keyword != old_keyword: raise ValueError("This keyword already exists")
                    payload={k:item.get(k) for k in ("text","media_type","media_file_id","buttons","buttons_input","enabled") if k in item}
                    await save_support_auto_reply(owner,new_keyword,**payload)
                    if new_keyword != old_keyword: await delete_support_auto_reply(owner,old_keyword)
                    context.user_data.clear()
                    await update.effective_message.reply_text("✅ Keyword changed",reply_markup=self.support_auto_reply_edit_menu(new_keyword,await get_support_auto_reply(owner,new_keyword)))
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}")
                return
            if context.user_data.get("wait_support_ar_text"):
                keyword=context.user_data["wait_support_ar_text"]
                await save_support_auto_reply(owner,keyword,text=text); context.user_data.clear()
                await update.effective_message.reply_text("✅ Text saved",reply_markup=self.support_auto_reply_edit_menu(keyword, await get_support_auto_reply(owner, keyword))); return
            if context.user_data.get("wait_support_ar_buttons"):
                keyword=context.user_data["wait_support_ar_buttons"]
                try: rows=_live_support_parse_buttons(text)
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}"); return
                await save_support_auto_reply(owner,keyword,buttons=rows,buttons_input=text); context.user_data.clear()
                await update.effective_message.reply_text("✅ URL buttons saved",reply_markup=self.support_auto_reply_edit_menu(keyword, await get_support_auto_reply(owner, keyword))); return
            if context.user_data.get("wait_support_tpl_command"):
                command=text.strip().lower().lstrip("/")
                try:
                    await save_support_template(owner,command)
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ {exc}"); return
                context.user_data.clear(); await update.effective_message.reply_text(f"✅ /{command} created",reply_markup=self.support_template_edit_menu(command, await get_support_template(owner, command))); return
            if context.user_data.get("wait_support_tpl_command_edit"):
                old_command=context.user_data["wait_support_tpl_command_edit"]
                new_command=text.strip().lower().lstrip("/")
                try:
                    tpl=await get_support_template(owner,old_command)
                    if not tpl: raise ValueError("Template not found")
                    if not new_command: raise ValueError("Keyword cannot be empty")
                    existing=await get_support_template(owner,new_command)
                    if existing and new_command != old_command: raise ValueError("This keyword already exists")
                    payload={k:tpl.get(k) for k in ("text","media_type","media_file_id","buttons","buttons_input","enabled","auto_delete_seconds","auto_delete_minutes") if k in tpl}
                    await save_support_template(owner,new_command,**payload)
                    if new_command != old_command: await delete_support_template(owner,old_command)
                    context.user_data.clear()
                    await update.effective_message.reply_text(f"✅ Keyword changed",reply_markup=self.support_template_edit_menu(new_command,await get_support_template(owner,new_command)))
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}")
                return
            if context.user_data.get("wait_support_tpl_text"):
                command=context.user_data["wait_support_tpl_text"]
                await save_support_template(owner,command,text=text); context.user_data.clear()
                await update.effective_message.reply_text("✅ Template text saved",reply_markup=self.support_template_edit_menu(command, await get_support_template(owner, command))); return
            if context.user_data.get("wait_support_tpl_buttons"):
                command=context.user_data["wait_support_tpl_buttons"]
                try: rows=_live_support_parse_buttons(text)
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}"); return
                await save_support_template(owner,command,buttons=rows,buttons_input=text); context.user_data.clear()
                await update.effective_message.reply_text("✅ Template buttons saved",reply_markup=self.support_template_edit_menu(command, await get_support_template(owner, command))); return
            if context.user_data.get("wait_support_tpl_auto_delete"):
                command=context.user_data["wait_support_tpl_auto_delete"]
                try:
                    seconds=_parse_auto_delete_duration(text)
                    await save_support_template(owner,command,auto_delete_seconds=seconds)
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ {exc}")
                    return
                context.user_data.clear()
                await update.effective_message.reply_text(
                    f"✅ Template Auto Remove updated\n\n/{command}: {_format_auto_delete(seconds)}",
                    reply_markup=self.support_template_edit_menu(command),
                ); return
            if context.user_data.get("wait_coupon_create"):
                try:
                    code,ctype,value,limit=[x.strip() for x in text.split("|",3)]
                    if ctype not in {"percent","fixed"}: raise ValueError("type")
                    await create_coupon(owner,code,ctype,float(value),int(limit))
                    context.user_data.clear(); await update.effective_message.reply_text("✅ Coupon saved",reply_markup=self.admin_menu())
                except Exception:
                    await update.effective_message.reply_text("❌ Use: SAVE20 | percent | 20 | 100")
                return
            if context.user_data.get("wait_plan_add") or context.user_data.get("wait_plan_edit"):
                try:
                    name,dtext,dmins,price,stars=self.parse_plan(text)
                    pid=context.user_data.get("wait_plan_edit")
                    if pid: await update_plan(owner,pid,name=name,duration_text=dtext,duration_minutes=dmins,price=price,stars_price=stars)
                    else: await create_plan(owner,name,dtext,dmins,price,stars)
                    context.user_data.clear(); await update.effective_message.reply_text("✅ Plan saved",reply_markup=self.plans_admin_menu())
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}")
                return
            if context.user_data.get("wait_channel"):
                try:
                    cid,name=[x.strip() for x in text.split("|",1)]; await add_channel(owner,int(cid),name,"group")
                    context.user_data.clear(); await update.effective_message.reply_text("✅ Channel/group added",reply_markup=self.channels_menu())
                except Exception: await update.effective_message.reply_text("❌ Use: -1001234567890 | Group Name")
                return
            if context.user_data.get("wait_upi_id") or context.user_data.get("wait_upi_name"):
                key="upi_id" if context.user_data.get("wait_upi_id") else "upi_name"
                await set_seller_setting(owner,key,text)
                context.user_data.clear()
                gateway_cfg=await get_gateway_config("seller",owner,decrypt=True)
                await update.effective_message.reply_text("✅ Updated",reply_markup=self.manual_payment_menu(bool(gateway_cfg.get("manual_enabled",True))))
                return
            if context.user_data.get("wait_currency"):
                code = normalize_currency(text)
                if not code:
                    supported = ", ".join(CURRENCY_INFO.keys())
                    await update.effective_message.reply_text(
                        f"❌ Unsupported currency code.\n\nSupported: {supported}\n\nSend one code, for example: USD",
                        reply_markup=self.back("a_settings"),
                    )
                    return
                await set_seller_setting(owner, "currency", code)
                context.user_data.clear()
                await update.effective_message.reply_text(
                    f"✅ Currency Updated\n\nNew currency: {currency_symbol(code)} {code} — {currency_name(code)}\n\n📌 All plan screens and manual-payment displays now use this currency label. Existing numeric prices were not converted.",
                    reply_markup=self.settings_menu(),
                )
                return
            mapping=[("wait_bot_name","bot_name",text,self.settings_menu()),("wait_support","support_username",text if text.startswith("@") else "@"+text,self.settings_menu())]
            for state,key,val,kb in mapping:
                if context.user_data.get(state): await set_seller_setting(owner,key,val); context.user_data.clear(); await update.effective_message.reply_text("✅ Updated",reply_markup=kb); return
            if context.user_data.get("wait_welcome_text"):
                await set_seller_setting(owner,"welcome_message",text); context.user_data.clear()
                await update.effective_message.reply_text("✅ Welcome text saved. Use 👀 Full Preview to check it.",reply_markup=self.welcome_text_menu(True)); return
            if context.user_data.get("wait_welcome_buttons"):
                try: rows=self.parse_welcome_buttons(text)
                except Exception as exc: await update.effective_message.reply_text(f"❌ {exc}"); return
                await set_seller_setting(owner,"welcome_buttons",rows); context.user_data.clear()
                await update.effective_message.reply_text("✅ Welcome buttons saved. Use 👀 Full Preview to check them.",reply_markup=self.welcome_buttons_menu(True)); return
            if context.user_data.get("wait_staff_promote"):
                try:
                    staff_user_id=int(text.strip())
                    if staff_user_id==self.seller_account(context):
                        raise ValueError("Seller is already the owner")
                    user=await get_user(owner,staff_user_id)
                    role=context.user_data["wait_staff_promote"]
                    record=await promote_staff(
                        owner, staff_user_id, role, update.effective_user.id,
                        username=(user or {}).get("username", ""),
                        full_name=(user or {}).get("full_name", ""),
                    )
                    context.user_data.clear()
                    try:
                        await context.bot.send_message(staff_user_id, f"✅ You were promoted as {role.title()} for this clone bot. Send /start to open your staff panel.")
                    except Exception:
                        pass
                    await update.effective_message.reply_text(
                        f"✅ Staff promoted\n\nUser ID: {staff_user_id}\nRole: {role.title()}",
                        reply_markup=self.staff_menu(),
                    )
                except Exception as exc:
                    await update.effective_message.reply_text(f"❌ Could not promote staff: {exc}\n\nSend a numeric Telegram User ID.")
                return

            if context.user_data.get("wait_user_search"):
                query=text.strip()
                user=None

                if query.startswith("@"):
                    user=await get_user_by_username(owner,query)
                else:
                    try:
                        user=await get_user(owner,int(query))
                    except ValueError:
                        user=await get_user_by_username(owner,query)

                if not user:
                    await update.effective_message.reply_text(
                        "❌ User not found. Send a valid User ID or @username.",
                        reply_markup=self.back("a_home"),
                    )
                    return

                context.user_data.clear()

                await self.show_user_details(
                    _MessageQueryAdapter(update.effective_message),
                    owner,
                    int(user["user_id"]),
                )
                return

            if context.user_data.get("wait_user_custom_duration"):
                user_id=int(context.user_data["wait_user_custom_duration"])
                value=text.strip().lower()
                try:
                    if value.endswith("mo"):
                        amount=int(value[:-2]); duration_minutes=amount*30*1440
                    elif value.endswith("y"):
                        amount=int(value[:-1]); duration_minutes=amount*365*1440
                    elif value.endswith("m"):
                        amount=int(value[:-1]); duration_minutes=amount
                    elif value.endswith("h"):
                        amount=int(value[:-1]); duration_minutes=amount*60
                    elif value.endswith("d"):
                        amount=int(value[:-1]); duration_minutes=amount*1440
                    else:
                        raise ValueError
                    if amount <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    await update.effective_message.reply_text(
                        "❌ Invalid duration. Use: 30m, 12h, 7d, 3mo or 1y.",
                        reply_markup=self.back(f"a_user_view_{user_id}"),
                    )
                    return

                seller_account_id = self.seller_account(context)
                plan_cfg, _ = await effective_plan(seller_account_id)
                active_now = await active_subscriptions(owner)
                already_active = any(int(x.get("user_id")) == user_id for x in active_now)
                sub_limit = int(plan_cfg.get("active_subscriber_limit", 25))
                if not already_active and sub_limit >= 0 and len(active_now) >= sub_limit:
                    context.user_data.clear()
                    await update.effective_message.reply_text(
                        await plan_limit_warning(seller_account_id),
                        reply_markup=self.limit_keyboard(f"a_user_view_{user_id}"),
                    )
                    return

                await activate_subscription(
                    owner, user_id, "Owner Assigned", duration_minutes,
                    amount=0, duration_text=value,
                )
                delivery=await self.deliver_subscription_access(owner,user_id)
                context.user_data.clear()
                try:
                    await context.bot.send_message(
                        user_id,
                        "🎉 Subscription activated/extended by admin.\n"
                        f"Duration added: {value}\n\n"
                        f"New invite links sent: {delivery.get('sent',0)}\n"
                        f"Already joined: {delivery.get('already_member',0)}",
                    )
                except Exception:
                    pass
                await self.show_user_details(
                    _MessageQueryAdapter(update.effective_message), owner, user_id,
                )
                return

            if context.user_data.get("wait_user_ban_reason"):
                user_id=int(context.user_data["wait_user_ban_reason"])
                await set_user_ban(owner,user_id,True,text)
                context.user_data.clear()

                try:
                    await context.bot.send_message(
                        user_id,
                        f"🚫 You have been banned.\nReason: {text}",
                    )
                except Exception:
                    pass

                await self.show_user_details(
                    _MessageQueryAdapter(update.effective_message),
                    owner,
                    user_id,
                )
                return

            if context.user_data.get("wait_timezone"):
                try:
                    timezone_name = normalize_timezone(text)
                except Exception:
                    await update.effective_message.reply_text(
                        "❌ Invalid timezone.\n\nUse the exact format, for example:\nAsia/Kolkata\n\nTimezone names are case-sensitive.",
                        reply_markup=timezone_keyboard("a_tz_", "a_settings"),
                    )
                    return
                await set_seller_setting(owner, "timezone", timezone_name)
                context.user_data.clear()
                await update.effective_message.reply_text(
                    f"✅ Timezone updated!\n\nTimezone: {timezone_name}",
                    reply_markup=self.settings_menu(),
                )
                return
            if context.user_data.get("wait_referral_unlock_duration"):
                try:
                    duration_days=int((update.effective_message.text or "").strip())
                except (TypeError,ValueError):
                    duration_days=0
                if duration_days < 1 or duration_days > 3650:
                    await update.effective_message.reply_text("❌ Send a whole number from 1 to 3650.",reply_markup=self.back("a_referral_unlock")); return
                await set_seller_setting(owner,"referral_unlock_duration_days",duration_days)
                context.user_data.clear()
                settings=await get_seller_settings(owner)
                channels=await get_channels(owner)
                await update.effective_message.reply_text(
                    self.referral_unlock_text(settings),
                    reply_markup=self.referral_unlock_menu(settings,channels),
                ); return
            if context.user_data.get("wait_referral_unlock_required"):
                try:
                    required=int((update.effective_message.text or "").strip())
                    if required < 1 or required > 100:
                        raise ValueError
                except ValueError:
                    await update.effective_message.reply_text("❌ Send a whole number from 1 to 100.",reply_markup=self.back("a_referral_unlock")); return
                await set_seller_setting(owner,"referral_unlock_required",required)
                context.user_data.clear()
                settings=await get_seller_settings(owner)
                channels=await get_channels(owner)
                await update.effective_message.reply_text(
                    self.referral_unlock_text(settings),
                    reply_markup=self.referral_unlock_menu(settings,channels),
                ); return

            if context.user_data.get("wait_referral_days"):
                try:
                    days=int(text)
                    if days < 0 or days > 3650:
                        raise ValueError
                except ValueError:
                    await update.effective_message.reply_text(
                        "❌ Send a number from 0 to 3650."
                    )
                    return

                await set_seller_setting(
                    owner,
                    "referral_reward_days",
                    days,
                )
                context.user_data.clear()
                await update.effective_message.reply_text(
                    f"✅ Referral reward set to {days} day(s).",
                    reply_markup=self.settings_menu(),
                )
                return
            if context.user_data.get("wait_reminder"):
                try: days=int(text)
                except ValueError: await update.effective_message.reply_text("❌ Send number"); return
                await set_seller_setting(owner,"reminder_days",days); context.user_data.clear(); await update.effective_message.reply_text("✅ Updated",reply_markup=self.settings_menu()); return

