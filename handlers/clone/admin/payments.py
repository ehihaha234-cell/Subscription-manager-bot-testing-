"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def _clone_qr_file_id(context, owner: int) -> str:
    bot_id = int(context.application.bot_data.get("seller_bot_id") or 0)
    qr = await get_bot_payment_qr(bot_id) if bot_id else ""
    if qr:
        return qr
    # Backward compatibility for the original clone whose QR lived only in
    # seller_settings.
    settings = await get_seller_settings(owner)
    return str(settings.get("upi_qr_file_id") or "")

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
    state = {'a_set_upi_id': ('wait_upi_id', 'Send UPI ID', 'a_manual_payment'), 'a_set_upi_name': ('wait_upi_name', 'Send UPI Name', 'a_manual_payment'), 'a_set_bot_name': ('wait_bot_name', 'Send Bot Name', 'a_settings'), 'a_set_support': ('wait_support', 'Send Support Username', 'a_settings'), 'a_set_currency': ('wait_currency', '__CURRENCY_GUIDE__', 'a_settings'), 'a_set_timezone': ('wait_timezone', '__TIMEZONE_PICKER__', 'a_settings'), 'a_set_reminder': ('wait_reminder', 'Send Reminder Days', 'a_settings'), 'a_set_referral_days': ('wait_referral_days', 'Send free reward days per successful referral', 'a_settings')}
    if a in state:
        key, msg, back = state[a]
        context.user_data.clear()
        if a == 'a_set_timezone':
            settings = await get_seller_settings(owner)
            await q.edit_message_text(timezone_guide(settings.get('timezone') or 'Asia/Kolkata'), reply_markup=timezone_keyboard('a_tz_', 'a_settings'))
        elif a == 'a_set_currency':
            settings = await get_seller_settings(owner)
            context.user_data['wait_currency'] = True
            await q.edit_message_text(currency_settings_text(settings.get('currency') or 'INR'), reply_markup=self.back('a_settings'))
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
        await q.edit_message_text(f"⚙️ Bot Settings\n\n🤖 Bot Name: {s.get('bot_name') or 'Not Set'}\n📞 Support: {s.get('support_username') or 'Not Set'}\n💱 Currency: {currency_symbol(s.get('currency') or 'INR')} {normalize_currency(s.get('currency')) or 'INR'} ({currency_name(s.get('currency') or 'INR')})\n🕒 Timezone: {s.get('timezone') or 'Asia/Kolkata'}\n🔔 Reminder: {s.get('reminder_days')} day(s)", reply_markup=self.settings_menu())
        return True
    if a == 'a_pending':
        ps = await pending_payments(owner)
        lines = ['📨 Pending Payments\n']
        kb = []
        for p in ps:
            lines.append(f"• {p['user_id']} | {format_currency((await get_seller_settings(owner)).get('currency'), p['amount'])} | {p['plan']}")
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
            changed = await set_payment_status(owner, pid, 'rejected', owner)
            if not changed:
                await q.answer('Payment is already being processed', show_alert=True)
                return True
            await context.bot.send_message(p['user_id'], '❌ Payment rejected')
            rejected_caption = await self.payment_details_caption(owner, p, status='rejected', processed_by=owner)
            await q.edit_message_caption(caption=rejected_caption, reply_markup=None)
            return True
        claimed = await claim_payment_for_processing(owner, pid, owner)
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
            finalized = await finalize_processed_payment(owner, pid, 'approved', owner)
            if not finalized:
                raise RuntimeError('Could not finalize payment status')
            expiry_text = self.format_dt(expiry)
            invoice = await create_invoice(owner, p['user_id'], p, (await get_seller_settings(owner)).get('bot_name', 'Seller'))
            await audit('child_payment_approved', owner, owner, {'payment_id': pid, 'invoice_no': invoice['invoice_no']})
            if was_already_active:
                status_text = f'ℹ️ Your subscription was already active.\nYour new payment has been added to your existing subscription.\n\n📅 Previous Expiry: {self.format_dt(previous_expiry)}\n📅 New Expiry: {expiry_text}\n\n🔗 A fresh private invite link has been generated for you.'
            else:
                status_text = f'📅 Expiry Date: {expiry_text}\n\n🔗 Your fresh private invite link has been generated.'
            await context.bot.send_message(p['user_id'], f"✅ Payment approved manually\n━━━━━━━━━━━━━━━━━━━━━━\n📦 Purchased Plan: {p['plan']}\n💰 Amount: {format_currency((await get_seller_settings(owner)).get('currency'), float(p.get('amount') or 0))}\n🧾 Payment ID: {pid}\n⌛ Added Duration: {p.get('duration_text') or '-'}\n🧾 Receipt/Invoice: {invoice['invoice_no']}\n━━━━━━━━━━━━━━━━━━━━━━\n\n{status_text}\n\nJoin using your private invite link(s):\n\n" + '\n\n'.join(links), disable_web_page_preview=True)
            approved_caption = await self.payment_details_caption(owner, p, status='approved', processed_by=owner)
            await q.edit_message_caption(caption=approved_caption, reply_markup=None)
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
        text = '📜 Payment History\n\n' + '\n'.join((f"{('✅' if p['status'] == 'approved' else '❌')} {p['user_id']} {format_currency((await get_seller_settings(owner)).get('currency'), p['amount'])} {p['plan']}" for p in ps[:20]))
        await q.edit_message_text(text, reply_markup=self.back())
        return True
    return False
