"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


def _actor_name(user):
    return " ".join(
        value for value in [getattr(user, "first_name", None), getattr(user, "last_name", None)]
        if value
    ).strip() or (f"@{user.username}" if getattr(user, "username", None) else "Unknown")


async def _clone_qr_file_id(context, owner: int) -> str:
    bot_id = int(context.application.bot_data.get("seller_bot_id") or 0)
    qr = await get_bot_payment_qr(bot_id) if bot_id else ""
    if qr:
        return qr
    # Backward compatibility for the original clone whose QR lived only in
    # seller_settings.
    settings = await get_seller_settings(owner)
    return str(settings.get("upi_qr_file_id") or "")

async def _update_payment_notification_messages(context, owner, payment_id, caption, current_message=None):
    """Reliably replace every pending-payment notification with its final status."""
    refs = list(await get_payment_notification_messages(owner, payment_id) or [])

    # The callback message is the most important one. Edit the Message object
    # directly first; this works even for older payments whose DB notification
    # references were never stored.
    if current_message is not None:
        try:
            await current_message.edit_caption(caption=caption, reply_markup=None)
            refs.append({
                "chat_id": int(current_message.chat_id),
                "message_id": int(current_message.message_id),
            })
        except TelegramError as exc:
            logger.warning(
                "Direct payment notification edit failed owner=%s payment=%s: %s",
                owner, payment_id, exc,
            )
            try:
                await current_message.edit_text(text=caption, reply_markup=None)
                refs.append({
                    "chat_id": int(current_message.chat_id),
                    "message_id": int(current_message.message_id),
                })
            except TelegramError:
                pass
        except (TypeError, ValueError, AttributeError):
            pass

    seen = set()
    updated = 0
    for ref in refs:
        try:
            chat_id = int(ref.get("chat_id"))
            message_id = int(ref.get("message_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)

        # The current callback message may already have been edited above.
        # Telegram will reject a second identical edit; skip it quietly.
        if current_message is not None and key == (int(current_message.chat_id), int(current_message.message_id)):
            updated += 1
            continue

        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=caption, reply_markup=None,
            )
            updated += 1
            continue
        except TelegramError as exc:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=caption, reply_markup=None,
                )
                updated += 1
                continue
            except TelegramError:
                logger.warning(
                    "Could not update payment notification owner=%s payment=%s chat=%s message=%s: %s",
                    owner, payment_id, chat_id, message_id, exc,
                )

    logger.info(
        "Payment notification status update owner=%s payment=%s updated=%s total_refs=%s",
        owner, payment_id, updated, len(seen),
    )
    return updated


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_payment':
        settings = await get_seller_settings(owner)
        qr_file_id = await _clone_qr_file_id(context, owner)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        gateways = gateway_cfg.get('gateways') or {}
        rz = gateways.get('razorpay') or {}
        cf = gateways.get('cashfree') or {}
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        stars_enabled = bool(gateway_cfg.get('stars_enabled', False))
        await q.edit_message_text(f"💳 Payment Settings\n\n{('✅' if rz.get('enabled') else '❌')} Razorpay: {('Enabled' if rz.get('enabled') else 'Disabled')} | Credentials: {('Added' if rz.get('key_id') and rz.get('key_secret') else 'Not added')}\n{('✅' if cf.get('enabled') else '❌')} Cashfree: {('Enabled' if cf.get('enabled') else 'Disabled')} | Credentials: {('Added' if cf.get('client_id') and cf.get('client_secret') else 'Not added')}\n{('✅' if manual_enabled else '❌')} Manual Payment: {('Enabled' if manual_enabled else 'Disabled')}\n{('✅' if stars_enabled else '❌')} Telegram Stars: {('Enabled' if stars_enabled else 'Disabled')}\n\nUPI ID: {settings.get('upi_id') or 'Not added'}\nUPI Name: {settings.get('upi_name') or 'Not added'}\nQR Code: {('Added ✅' if qr_file_id else 'Not added ❌')}", reply_markup=self.payment_menu())
        return True
    if a == 'a_stars_toggle':
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        await set_gateway_preferences(
            'seller', owner,
            stars_enabled=not bool(gateway_cfg.get('stars_enabled', False)),
        )
        settings = await get_seller_settings(owner)
        qr_file_id = await _clone_qr_file_id(context, owner)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        gateways = gateway_cfg.get('gateways') or {}
        rz = gateways.get('razorpay') or {}
        cf = gateways.get('cashfree') or {}
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        stars_enabled = bool(gateway_cfg.get('stars_enabled', False))
        await q.edit_message_text(
            f"💳 Payment Settings\n\n"
            f"{('✅' if rz.get('enabled') else '❌')} Razorpay: {('Enabled' if rz.get('enabled') else 'Disabled')} | Credentials: {('Added' if rz.get('key_id') and rz.get('key_secret') else 'Not added')}\n"
            f"{('✅' if cf.get('enabled') else '❌')} Cashfree: {('Enabled' if cf.get('enabled') else 'Disabled')} | Credentials: {('Added' if cf.get('client_id') and cf.get('client_secret') else 'Not added')}\n"
            f"{('✅' if manual_enabled else '❌')} Manual Payment: {('Enabled' if manual_enabled else 'Disabled')}\n"
            f"{('✅' if stars_enabled else '❌')} Telegram Stars: {('Enabled' if stars_enabled else 'Disabled')}\n\n"
            f"UPI ID: {settings.get('upi_id') or 'Not added'}\n"
            f"UPI Name: {settings.get('upi_name') or 'Not added'}\n"
            f"QR Code: {('Added ✅' if qr_file_id else 'Not added ❌')}",
            reply_markup=self.payment_menu(),
        )
        return True
    if a == 'a_manual_payment':
        settings = await get_seller_settings(owner)
        qr_file_id = await _clone_qr_file_id(context, owner)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        await q.edit_message_text(f"💵 Manual Payment\n\nUPI ID: {settings.get('upi_id') or 'Not added'}\nUPI Name: {settings.get('upi_name') or 'Not added'}\nQR Code: {('Added ✅' if qr_file_id else 'Not added ❌')}", reply_markup=self.manual_payment_menu(manual_enabled))
        return True
    if a == 'a_manual_toggle':
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        await set_gateway_preferences('seller', owner, manual_enabled=not gateway_cfg.get('manual_enabled', True))
        settings = await get_seller_settings(owner)
        qr_file_id = await _clone_qr_file_id(context, owner)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        await q.edit_message_text(f"💵 Manual Payment\n\nUPI ID: {settings.get('upi_id') or 'Not added'}\nUPI Name: {settings.get('upi_name') or 'Not added'}\nQR Code: {('Added ✅' if qr_file_id else 'Not added ❌')}", reply_markup=self.manual_payment_menu(manual_enabled))
        return True
    if a == 'a_remove_qr':
        bot_id = int(context.application.bot_data.get("seller_bot_id") or 0)
        clone_record = await get_bot_by_bot_id(bot_id) if bot_id else None
        if bot_id:
            await set_bot_payment_qr(bot_id, "")
        # Do not clear another clone's legacy QR. Only the original clone whose
        # database scope is the seller account owns the old shared setting.
        if clone_record and int(clone_record.get('data_owner_id') or 0) == int(owner):
            await set_seller_setting(owner, 'upi_qr_file_id', '')
        settings = await get_seller_settings(owner)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        await q.edit_message_text(f"💵 Manual Payment\n\nUPI ID: {settings.get('upi_id') or 'Not added'}\nUPI Name: {settings.get('upi_name') or 'Not added'}\nQR Code: Not added ❌", reply_markup=self.manual_payment_menu(manual_enabled))
        return True
    if a == 'a_payment_preview':
        settings = await get_seller_settings(owner)
        qr_file_id = await _clone_qr_file_id(context, owner)
        preview = f"💳 Payment Details\n\nUPI ID: {settings.get('upi_id') or 'Not Set'}\nUPI Name: {settings.get('upi_name') or 'Not Set'}\nQR Code: {('Added' if qr_file_id else 'Not Added')}"
        preview_kb = self.back('a_manual_payment')
        if qr_file_id:
            await q.message.reply_photo(qr_file_id, caption=preview, reply_markup=preview_kb)
        else:
            await q.edit_message_text(preview, reply_markup=preview_kb)
        return True
    state = {'a_set_upi_id': ('wait_upi_id', 'Send UPI ID', 'a_manual_payment'), 'a_set_upi_name': ('wait_upi_name', 'Send UPI Name', 'a_manual_payment'), 'a_set_bot_name': ('wait_bot_name', 'Send Bot Name', 'a_settings'), 'a_set_support': ('wait_support', 'Send Support Username', 'a_settings'), 'a_set_currency': ('wait_currency', 'Send Currency', 'a_settings'), 'a_set_timezone': ('wait_timezone', '__TIMEZONE_PICKER__', 'a_settings'), 'a_set_reminder': ('wait_reminder', 'Send Reminder Days', 'a_settings'), 'a_set_referral_days': ('wait_referral_days', 'Send free reward days per successful referral', 'a_settings')}
    if a in state:
        key, msg, back = state[a]
        context.user_data.clear()
        if a == 'a_set_timezone':
            settings = await get_seller_settings(owner)
            await q.edit_message_text(timezone_guide(settings.get('timezone') or 'Asia/Kolkata'), reply_markup=timezone_keyboard('a_tz_', 'a_settings'))
        else:
            context.user_data[key] = True
            await q.edit_message_text(msg, reply_markup=self.back(back))
        return True
    if a == 'a_set_qr':
        context.user_data.clear()
        context.user_data['wait_qr'] = True
        await q.edit_message_text('Send QR image', reply_markup=self.back('a_manual_payment'))
        return True
    if a == 'a_settings':
        s = await get_seller_settings(owner)
        # Prefetch Welcome Message settings so its callbacks can render immediately.
        context.chat_data['_welcome_settings_cache'] = dict(s)
        await q.edit_message_text(f"⚙ Bot Settings\n\nBot Name: {s.get('bot_name')}\nSupport: {s.get('support_username') or 'Not Set'}\nCurrency: {s.get('currency')}\nTimezone: {s.get('timezone')}\nReminder: {s.get('reminder_days')}", reply_markup=self.settings_menu())
        return True
    if a == 'a_pending':
        ps = await pending_payments(owner)
        lines = ['📨 Pending Payments\n']
        kb = []
        for p in ps:
            lines.append(f"• {p['user_id']} | ₹{p['amount']:g} | {p['plan']}")
            kb.append([InlineKeyboardButton(f"View {p['user_id']}", callback_data=f"a_pay_view_{p['payment_id']}")])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_home')])
        await q.edit_message_text('\n'.join(lines) if ps else '📨 No pending payments', reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_pay_view_'):
        p = await get_payment(owner, a.replace('a_pay_view_', ''))
        if not p:
            await q.edit_message_text('Not found', reply_markup=self.admin_menu())
            return True
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve', callback_data=f"a_pay_ok_{p['payment_id']}"), InlineKeyboardButton('❌ Reject', callback_data=f"a_pay_no_{p['payment_id']}")], [InlineKeyboardButton('⬅ Back', callback_data='a_pending')]])
        caption = await self.payment_details_caption(owner, p, status=p.get('status', 'pending'))
        await q.message.reply_photo(p['screenshot_file_id'], caption=caption, reply_markup=kb)
        return True
    if a.startswith('a_pay_ok_') or a.startswith('a_pay_no_'):
        approve = a.startswith('a_pay_ok_')
        pid = a.replace('a_pay_ok_' if approve else 'a_pay_no_', '', 1)
        p = await get_payment(owner, pid)
        if not p:
            await q.answer('Payment not found', show_alert=True)
            return True
        current_status = p.get('status', 'pending')
        if current_status in {'approved', 'rejected'}:
            final_caption = await self.payment_details_caption(owner, p, status=current_status, processed_by=p.get('admin_id'))
            try:
                await q.edit_message_caption(caption=final_caption, reply_markup=None)
            except BadRequest:
                pass
            await q.answer(f'Already {current_status}', show_alert=True)
            return True
        if not approve:
            changed = await set_payment_status(owner, pid, 'rejected', q.from_user.id, _actor_name(q.from_user))
            if not changed:
                await q.answer('Payment is already being processed', show_alert=True)
                return True
            p = await get_payment(owner, pid) or p
            rejected_caption = await self.payment_details_caption(owner, p, status='rejected', processed_by=q.from_user.id)
            # The decision is already committed. Update the staff message first
            # so the callback feels immediate; user notification follows.
            await _update_payment_notification_messages(
                context, owner, p.get('payment_id'), rejected_caption, current_message=q.message
            )
            try:
                await context.bot.send_message(p['user_id'], '❌ Payment rejected')
            except Exception:
                logger.exception('Rejected-payment user notification failed owner=%s payment=%s', owner, pid)
            return True
        claimed = await claim_payment_for_processing(owner, pid, owner)
        if claimed:
            # Remove the buttons and immediately acknowledge the callback in the
            # staff chat while the existing fulfillment flow continues unchanged.
            try:
                processing_caption = await self.payment_details_caption(
                    owner, p, status='pending'
                )
                await q.edit_message_caption(
                    caption=processing_caption + '\n\n⏳ Approval is being processed...',
                    reply_markup=None,
                )
            except Exception:
                logger.debug(
                    'Could not show payment processing state owner=%s payment=%s',
                    owner, pid, exc_info=True,
                )
        if not claimed:
            latest = await get_payment(owner, pid)
            latest_status = (latest or {}).get('status', 'unknown')
            await q.answer(f'Payment status: {latest_status}', show_alert=True)
            return True
        try:
            seller_account_id = self.seller_account(context)
            plan_cfg, _ = await effective_plan(seller_account_id)
            active_now = await active_subscriptions(owner)
            already_active = any((int(x.get('user_id')) == int(p['user_id']) for x in active_now))
            sub_limit = int(plan_cfg.get('active_subscriber_limit', 25))
            if not already_active and sub_limit >= 0 and (len(active_now) >= sub_limit):
                await release_processing_payment(owner, pid, 'seller subscriber limit reached')
                await q.answer('Seller plan limit reached', show_alert=True)
                await context.bot.send_message(seller_account_id, await plan_limit_warning(seller_account_id), reply_markup=self.limit_keyboard('a_pending'))
                return True
            previous_sub = await get_subscription(owner, p['user_id'])
            now = datetime.now(timezone.utc)
            previous_expiry = (previous_sub or {}).get('expiry_date')
            if previous_expiry and previous_expiry.tzinfo is None:
                previous_expiry = previous_expiry.replace(tzinfo=timezone.utc)
            was_already_active = bool(previous_sub and previous_sub.get('active') and previous_expiry and (previous_expiry > now))
            manual_fulfillment = await fulfill_subscription_payment(owner, p['user_id'], f'manual:{owner}:{pid}', p['plan'], p['duration_minutes'], amount=p.get('amount'), duration_text=p.get('duration_text'))
            expiry = manual_fulfillment.get('expiry_date')
            referral = await mark_referral_rewarded(owner, p['user_id'], payment_id=pid)
            if referral:
                settings = await get_seller_settings(owner)
                reward_days = int(settings.get('referral_reward_days', 7) or 0)
                referrer_id = int(referral['referrer_user_id'])
                try:
                    if reward_days > 0:
                        await activate_subscription(owner, referrer_id, 'Referral Reward', reward_days * 1440, amount=0, duration_text=f'{reward_days}d')
                    finalized_reward = await finalize_referral_reward(owner, p['user_id'], payment_id=pid)
                    if not finalized_reward:
                        raise RuntimeError('Referral reward finalization was not applied')
                    if reward_days > 0:
                        try:
                            await context.bot.send_message(referrer_id, f'🎉 Referral Reward Added!\nYou received {reward_days} free day(s).')
                        except Exception:
                            logger.exception('Referral reward notification failed owner=%s referrer=%s payment=%s', owner, referrer_id, pid)
                except Exception as exc:
                    await release_referral_reward(owner, p['user_id'], str(exc), payment_id=pid)
                    logger.exception('Referral reward processing failed owner=%s referred=%s payment=%s', owner, p['user_id'], pid)
            links = []
            for ch in await get_channels(owner):
                try:
                    inv = await context.bot.create_chat_invite_link(ch['chat_id'], member_limit=1)
                    await save_invite(owner, p['user_id'], ch['chat_id'], inv.invite_link)
                    links.append(f"{ch.get('title')}: {inv.invite_link}")
                except Exception as exc:
                    links.append(f"{ch.get('title')}: invite failed ({exc})")
            finalized = await finalize_processed_payment(owner, pid, 'approved', q.from_user.id, _actor_name(q.from_user))
            if not finalized:
                raise RuntimeError('Could not finalize payment status')
            # Reload after finalization so the user-facing audit details use
            # the actual approval timestamp written to MongoDB.
            p = await get_payment(owner, pid) or p
            expiry_text = self.format_dt(expiry)
            invoice = await create_invoice(owner, p['user_id'], p, (await get_seller_settings(owner)).get('bot_name', 'Seller'))
            await audit('child_payment_approved', owner, owner, {'payment_id': pid, 'invoice_no': invoice['invoice_no']})
            if was_already_active:
                status_text = f'ℹ️ Your subscription was already active.\nYour new payment has been added to your existing subscription.\n\n📅 Previous Expiry: {self.format_dt(previous_expiry)}\n📅 New Expiry: {expiry_text}\n\n🔗 A fresh private invite link has been generated for you.'
            else:
                status_text = f'📅 Expiry Date: {expiry_text}\n\n🔗 Your fresh private invite link has been generated.'
            # Keep the existing approval/fulfillment flow unchanged. Only enrich
            # the user-facing manual approval message with payment audit details.
            approved_user = await get_user(owner, int(p['user_id'])) or {}
            approved_name = " ".join(
                value for value in [
                    approved_user.get("first_name"),
                    approved_user.get("last_name"),
                ] if value
            ).strip() or "Unknown"
            approved_username = (
                f"@{approved_user.get('username')}"
                if approved_user.get("username")
                else "Not set"
            )
            submitted_text = self.format_dt(p.get("created_at"))
            approved_at_text = self.format_dt(
                p.get("approved_at") or p.get("processed_at")
            )

            await context.bot.send_message(
                p['user_id'],
                f"✅ Payment approved manually\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Purchased Plan: {p['plan']}\n"
                f"💰 Amount: ₹{float(p.get('amount') or 0):g}\n"
                f"🧾 Payment ID: {pid}\n"
                f"⌛ Added Duration: {p.get('duration_text') or '-'}\n"
                f"🧾 Receipt/Invoice: {invoice['invoice_no']}\n"
                f"\n"
                f"👤 User ID: {p.get('user_id')}\n"
                f"👤 User: {approved_name}\n"
                f"🔗 Username: {approved_username}\n"
                f"📅 Submitted: {submitted_text}\n"
                f"✅ Approved At: {approved_at_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"\n{status_text}\n"
                f"\nJoin using your private invite link(s):\n\n"
                + '\n\n'.join(links),
                disable_web_page_preview=True,
            )
            p = await get_payment(owner, pid) or p
            approved_caption = await self.payment_details_caption(owner, p, status='approved', processed_by=q.from_user.id)
            await _update_payment_notification_messages(
                context, owner, p.get('payment_id'), approved_caption, current_message=q.message
            )
        except Exception as exc:
            logger.exception('Payment approval failed owner=%s payment=%s', owner, pid)
            await release_processing_payment(owner, pid, str(exc))
            await q.answer('Approval failed. Payment is still pending; you can press Approve again.', show_alert=True)
            try:
                await q.edit_message_caption(caption=await self.payment_details_caption(owner, p, status='pending') + '\n\n⚠️ Last approval attempt failed. Payment was kept pending safely.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve', callback_data=f'a_pay_ok_{pid}'), InlineKeyboardButton('❌ Reject', callback_data=f'a_pay_no_{pid}')]]))
            except Exception:
                pass
        return True
    if a == 'a_history':
        ps = await payment_history(owner)
        text = '📜 Payment History\n\n' + '\n'.join((f"{('✅' if p['status'] == 'approved' else '❌')} {p['user_id']} ₹{p['amount']:g} {p['plan']}" for p in ps[:20]))
        await q.edit_message_text(text, reply_markup=self.back())
        return True
    return False
