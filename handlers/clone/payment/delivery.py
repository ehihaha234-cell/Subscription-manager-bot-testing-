"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from database.business_delivery import list_business_contact_routes, log_business_payment_delivery


class ClonePaymentDeliveryMixin:
    async def stars_precheckout(self, update, context):
        query = update.pre_checkout_query
        try:
            kind, scope, owner_text, user_text, plan_id = str(query.invoice_payload or '').split(':', 4)
            owner = int(owner_text)
            user_id = int(user_text)
            if kind != 'stars' or scope != 'clone' or owner != self.owner(context) or user_id != int(query.from_user.id):
                raise ValueError('invalid invoice')
            cfg = await get_gateway_config('seller', owner, decrypt=True)
            plan = await get_plan(owner, plan_id)
            expected = int((plan or {}).get('stars_price', 0) or 0)
            if not cfg.get('stars_enabled') or not plan or expected <= 0 or query.currency != 'XTR' or query.total_amount != expected:
                raise ValueError('plan changed')
            await query.answer(ok=True)
        except Exception:
            await query.answer(ok=False, error_message='This Stars invoice is no longer valid. Reopen the payment page.')

    async def stars_success(self, update, context):
        payment = update.effective_message.successful_payment
        if not payment or payment.currency != 'XTR':
            return
        try:
            kind, scope, owner_text, user_text, plan_id = payment.invoice_payload.split(':', 4)
            owner = int(owner_text)
            user_id = int(user_text)
            if kind != 'stars' or scope != 'clone' or owner != self.owner(context) or user_id != int(update.effective_user.id):
                raise ValueError('invalid payment')
            plan = await get_plan(owner, plan_id)
            expected = int((plan or {}).get('stars_price', 0) or 0)
            if not plan or expected <= 0 or payment.total_amount != expected:
                raise ValueError('price mismatch')
            reference = payment.telegram_payment_charge_id
            previous = await get_subscription(owner, user_id)
            now = datetime.now(timezone.utc)
            previous_expiry = (previous or {}).get('expiry_date')
            if previous_expiry and previous_expiry.tzinfo is None:
                previous_expiry = previous_expiry.replace(tzinfo=timezone.utc)
            was_active = bool(previous and previous.get('active') and previous_expiry and previous_expiry > now)
            await create_automatic_payment(owner, user_id, plan, 'telegram_stars', reference, reference)
            result = await fulfill_subscription_payment(
                owner,
                user_id,
                f'stars:{reference}',
                plan['name'],
                plan['duration_minutes'],
                amount=0,
                duration_text=plan['duration_text'],
            )
            details = {
                'plan_name': plan['name'],
                'amount': 0,
                'stars_amount': payment.total_amount,
                'gateway': 'Telegram Stars',
                'transaction_id': reference,
                'payment_date': now,
                'expiry_date': result.get('expiry_date'),
                'duration': plan['duration_text'],
                'was_already_active': was_active,
                'previous_expiry': previous_expiry,
            }
            await self.deliver_subscription_access(owner, user_id, details)
            await self.notify_automatic_payment_success(owner, user_id, details)
        except Exception:
            logger.exception('Clone Stars fulfillment failed owner=%s user=%s', context.application.bot_data.get('seller_owner_id'), update.effective_user.id)
            await update.effective_message.reply_text(
                '⚠️ Your Stars payment was received, but activation needs support review. Keep this receipt and contact support.'
            )

    async def notify_automatic_payment_success(self, owner_id:int, user_id:int, details:dict):
        """Notify the seller and payment-authorized staff through the same clone bot."""
        owner_id = int(owner_id)
        user_id = int(user_id)
        running = self.get_running(owner_id)
        if not running:
            record = await get_bot_by_data_owner_id(owner_id)
            started = await self.start_bot(int(record["bot_id"])) if record else False
            running = self.get_running(owner_id) if started else None
        if not running:
            return {"sent": 0, "failed": 0, "error": "Clone bot is not running"}

        bot = running.application.bot
        timezone_name = await self.seller_timezone(owner_id)
        seller_account_id = int(
            running.application.bot_data.get("seller_account_id", owner_id)
        )

        try:
            user_chat = await bot.get_chat(user_id)
            full_name = getattr(user_chat, "full_name", None) or str(details.get("full_name") or "Unknown")
            username = getattr(user_chat, "username", None) or details.get("username")
        except TelegramError:
            full_name = str(details.get("full_name") or "Unknown")
            username = details.get("username")

        def _format_dt(value):
            return self.format_dt(value, timezone_name, "%d %b %Y, %I:%M %p %Z")

        safe_name = html.escape(full_name)
        safe_username = html.escape(f"@{username}" if username else "Not Set")
        mention = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
        amount = float(details.get("amount") or 0)
        settings = await get_seller_settings(owner_id)
        currency = settings.get('currency')
        stars_amount = int(details.get("stars_amount") or 0)
        gateway = str(details.get("gateway") or "-").title()
        amount_line = f"• Amount: ⭐{stars_amount}\n" if stars_amount else f"• Amount: {format_currency(currency, amount)}\n"

        text = (
            "💰 <b>Automatic Payment Successful</b>\n\n"
            "A subscriber payment has been verified automatically.\n\n"
            "👤 <b>User Details</b>\n"
            f"• Name: {safe_name}\n"
            f"• Username: {safe_username}\n"
            f"• Mention: {mention}\n"
            f"• User ID: <code>{user_id}</code>\n\n"
            "📦 <b>Subscription Details</b>\n"
            f"• Plan: {html.escape(str(details.get('plan_name') or 'Subscription'))}\n"
            f"• Duration: {html.escape(str(details.get('duration') or '-'))}\n"
            f"{amount_line}"
            f"• Payment Gateway: {html.escape(gateway)}\n"
            f"• Payment Date: {_format_dt(details.get('payment_date'))}\n"
            f"• Expiry Date: {_format_dt(details.get('expiry_date'))}\n\n"
            "🧾 <b>Payment Details</b>\n"
            f"• Transaction ID: <code>{html.escape(str(details.get('transaction_id') or '-'))}</code>\n"
            f"• Invoice: <code>{html.escape(str(details.get('invoice_no') or '-'))}</code>\n"
            "• Status: ✅ Paid & Activated\n\n"
            "✅ The user's subscription has been activated automatically."
        )

        recipients = {seller_account_id}
        try:
            for staff in await list_staff(owner_id):
                if staff.get("status") != "active":
                    continue
                permissions = staff.get("permissions") or []
                if "*" in permissions or "payments" in permissions:
                    recipients.add(int(staff["user_id"]))
        except Exception:
            logger.exception("Failed to load payment notification staff owner=%s", owner_id)

        sent = 0
        failed = 0
        for recipient_id in recipients:
            try:
                await bot.send_message(
                    chat_id=recipient_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                sent += 1
            except TelegramError as exc:
                failed += 1
                logger.warning(
                    "Automatic payment notification failed owner=%s recipient=%s user=%s: %s",
                    owner_id, recipient_id, user_id, exc,
                )

        return {"sent": sent, "failed": failed, "error": ""}

    async def _deliver_access_to_business_chats(
        self,
        bot,
        owner_id:int,
        user_id:int,
        text:str,
        *,
        send_bot_start_request:bool=False,
    ) -> dict:
        """Mirror the complete payment receipt to Official Business chats."""
        sent = 0
        failed = 0
        start_sent = 0
        start_failed = 0
        reasons = []
        routes = await list_business_contact_routes(int(owner_id), int(user_id))
        seen = set()

        if not routes:
            return {
                "sent": 0, "failed": 0, "start_sent": 0, "start_failed": 0,
                "routes_found": 0, "reason": "recipient_missing",
            }

        bot_username = ""
        if send_bot_start_request:
            try:
                bot_user = await bot.get_me()
                bot_username = str(getattr(bot_user, "username", "") or "").strip()
            except Exception as exc:
                reasons.append(f"bot_username_lookup:{exc}")

        for route in routes:
            mode = str(route.get("mode") or "")
            connection_id = str(route.get("connection_id") or "")
            chat_id = int(route.get("chat_id") or user_id)
            route_key = (mode, connection_id, chat_id)
            if route_key in seen:
                continue
            seen.add(route_key)

            if mode != "official" or not connection_id:
                continue

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    business_connection_id=connection_id,
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception as exc:
                failed += 1
                reasons.append(f"receipt:{type(exc).__name__}:{exc}")
                logger.exception(
                    "Business payment receipt failed owner=%s user=%s connection=%s chat=%s",
                    owner_id, user_id, connection_id, chat_id,
                )
                # Do not send the start prompt when the main receipt itself failed.
                continue

            if send_bot_start_request and bot_username:
                try:
                    start_url = f"https://t.me/{bot_username}?start=business_payment"
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "🤖 For subscription management and future updates, "
                            "please start the subscription bot."
                        ),
                        business_connection_id=connection_id,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("Start Subscription Bot", url=start_url)
                        ]]),
                        disable_web_page_preview=True,
                    )
                    start_sent += 1
                except Exception as exc:
                    start_failed += 1
                    reasons.append(f"start_button:{type(exc).__name__}:{exc}")
                    logger.exception(
                        "Business bot-start request failed owner=%s user=%s connection=%s chat=%s",
                        owner_id, user_id, connection_id, chat_id,
                    )

        reason = "; ".join(reasons)[:500]
        if not sent and not failed:
            reason = reason or "no_active_official_route"
        return {
            "sent": sent,
            "failed": failed,
            "start_sent": start_sent,
            "start_failed": start_failed,
            "routes_found": len(routes),
            "reason": reason,
        }

    async def deliver_subscription_access(self, owner_id:int, user_id:int, success_details:dict|None=None):
        """Send fresh invite links only for chats the user has not joined yet.

        When ``success_details`` is supplied by an automatic gateway payment,
        the access message also includes the user and subscription receipt
        details. Manual/admin delivery keeps the existing compact message.
        """
        running=self.get_running(int(owner_id))
        if not running:
            record=await get_bot_by_data_owner_id(int(owner_id))
            started=await self.start_bot(int(record["bot_id"])) if record else False
            running=self.get_running(int(owner_id)) if started else None
        if not running:
            return {"sent":0,"already_member":0,"failed":0,"error":"Clone bot is not running"}

        bot=running.application.bot
        timezone_name = await self.seller_timezone(int(owner_id))
        connected_channels=await get_channels(int(owner_id))
        if not connected_channels:
            return {"sent":0,"already_member":0,"failed":0,"error":"No channel/group is connected to this clone bot"}

        # Existing channel documents default to enabled so current sellers keep
        # their previous behaviour until they explicitly disable a destination.
        channels=[
            channel for channel in connected_channels
            if channel.get("auto_invite_enabled",True) is not False
        ]
        if not channels:
            return {
                "sent":0,
                "already_member":0,
                "failed":0,
                "error":"Automatic invite delivery is disabled for every connected channel/group",
            }

        links=[]
        already_member=0
        failed=0

        for ch in channels:
            chat_id=int(ch["chat_id"])
            try:
                member=await bot.get_chat_member(chat_id,int(user_id))
                status=getattr(member,"status","")
                is_member=getattr(member,"is_member",None)
                if status in {"creator","administrator","member"} or (status=="restricted" and is_member is not False):
                    # Keep membership information for delivery statistics, but do
                    # not skip link creation. Every successful new payment gets a
                    # fresh private invite link, even when the user is already in
                    # the connected channel/group.
                    already_member+=1
                if status=="kicked":
                    try:
                        await bot.unban_chat_member(chat_id,int(user_id),only_if_banned=True)
                    except TelegramError:
                        pass
            except BadRequest:
                pass
            except TelegramError as exc:
                logger.warning("Membership check failed owner=%s chat=%s user=%s: %s",owner_id,chat_id,user_id,exc)

            try:
                invite=await bot.create_chat_invite_link(
                    chat_id=chat_id,
                    member_limit=1,
                    name=f"Subscription access {user_id}",
                )
                await save_invite(owner_id, user_id, chat_id, invite.invite_link)
                links.append(f"📢 {ch.get('title','Premium Channel/Group')}\n{invite.invite_link}")
            except TelegramError as exc:
                failed+=1
                logger.warning("Invite creation failed owner=%s chat=%s user=%s: %s",owner_id,chat_id,user_id,exc)

        if links:
            try:
                if success_details:
                    try:
                        chat = await bot.get_chat(int(user_id))
                        full_name = getattr(chat, "full_name", None) or "Unknown"
                        username = getattr(chat, "username", None)
                    except TelegramError:
                        full_name = str(success_details.get("full_name") or "Unknown")
                        username = success_details.get("username")

                    username_text = f"@{username}" if username else "Not Set"

                    def _format_dt(value):
                        return self.format_dt(value, timezone_name, "%d %b %Y, %I:%M %p %Z")

                    was_already_active = bool(success_details.get("was_already_active"))
                    if was_already_active:
                        subscription_note = (
                            "ℹ️ Your subscription was already active.\n"
                            "Your new purchase has been added to your existing subscription.\n\n"
                            f"📅 Previous Expiry: {_format_dt(success_details.get('previous_expiry'))}\n"
                            f"📅 New Expiry: {_format_dt(success_details.get('expiry_date'))}\n\n"
                            "🔗 A fresh private invite link has been generated for you."
                        )
                    else:
                        subscription_note = (
                            f"⏳ Expiry Date: {_format_dt(success_details.get('expiry_date'))}\n\n"
                            "🔗 Your fresh private invite link has been generated."
                        )

                    success_amount_line = (
                        f"💰 Amount: ⭐{int(success_details.get('stars_amount') or 0)}\n"
                        if success_details.get('stars_amount')
                        else f"💰 Amount: {format_currency((await get_seller_settings(owner_id)).get('currency'), float(success_details.get('amount') or 0))}\n"
                    )
                    text = (
                        "✅ Payment verified automatically\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 Name: {full_name}\n"
                        f"🆔 Username: {username_text}\n"
                        f"📦 Purchased Plan: {success_details.get('plan_name') or 'Subscription'}\n"
                        f"{success_amount_line}"
                        f"💳 Gateway: {str(success_details.get('gateway') or '').title() or '-'}\n"
                        f"🧾 Transaction ID: {success_details.get('transaction_id') or '-'}\n"
                        f"📅 Payment Date: {_format_dt(success_details.get('payment_date'))}\n"
                        f"⌛ Added Duration: {success_details.get('duration') or '-'}\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{subscription_note}\n\n"
                        "Join using your private invite link(s):\n\n"
                        + "\n\n".join(links)
                    )
                else:
                    text = (
                        "✅ Your subscription has been updated.\n\n"
                        "Use the fresh invite link(s) below to join the channel/group(s) you have not joined yet:\n\n"
                        + "\n\n".join(links)
                    )

                bot_dm_error = ""
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=text,
                        disable_web_page_preview=True,
                    )
                except TelegramError as exc:
                    bot_dm_error = str(exc)
                    logger.warning(
                        "Clone bot access DM failed owner=%s user=%s: %s",
                        owner_id, user_id, exc,
                    )

                business_delivery = await self._deliver_access_to_business_chats(
                    bot,
                    int(owner_id),
                    int(user_id),
                    text,
                    send_bot_start_request=bool(success_details),
                )
                try:
                    await log_business_payment_delivery(
                        int(owner_id),
                        int(user_id),
                        bot_status="failed" if bot_dm_error else "sent",
                        business_status=(
                            "sent" if business_delivery.get("sent")
                            else "failed" if business_delivery.get("failed")
                            else "recipient_missing"
                        ),
                        start_button_status=(
                            "sent" if business_delivery.get("start_sent")
                            else "failed" if business_delivery.get("start_failed")
                            else "not_sent"
                        ),
                        business_reason=str(business_delivery.get("reason") or ""),
                        routes_found=int(business_delivery.get("routes_found") or 0),
                        transaction_id=str((success_details or {}).get("transaction_id") or ""),
                    )
                except Exception:
                    logger.exception(
                        "Payment delivery logging failed owner=%s user=%s", owner_id, user_id
                    )
                if bot_dm_error and not business_delivery.get("sent"):
                    return {
                        "sent": 0,
                        "already_member": already_member,
                        "failed": failed + len(links) + int(business_delivery.get("failed") or 0),
                        "error": bot_dm_error,
                    }
            except Exception as exc:
                logger.exception("Access message construction/delivery failed owner=%s user=%s", owner_id, user_id)
                return {"sent":0,"already_member":already_member,"failed":failed+len(links),"error":str(exc)}

        error = ""
        if not links and already_member == 0:
            error = "Invite link could not be created for any connected channel/group"
        elif failed and not links:
            error = "Invite link creation failed for all connected channel/groups"
        return {"sent":len(links),"already_member":already_member,"failed":failed,"error":error}

