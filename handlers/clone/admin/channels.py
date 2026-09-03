"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def _telegram_retry(operation, attempts=4, base_delay=1.0):
    """Retry transient Telegram API failures, including RetryAfter/flood waits."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                raise
            retry_after = getattr(exc, 'retry_after', None)
            try:
                delay = float(retry_after) + 0.5 if retry_after is not None else min(8.0, base_delay * (2 ** attempt))
            except (TypeError, ValueError):
                delay = min(8.0, base_delay * (2 ** attempt))
            await asyncio.sleep(max(0.75, delay))
    raise last_exc


async def _create_invite_with_retry(context, chat_id):
    return await _telegram_retry(
        lambda: context.bot.create_chat_invite_link(chat_id=chat_id, member_limit=1),
        attempts=4,
        base_delay=1.0,
    )


async def _send_message_with_retry(context, user_id, text, disable_web_page_preview=True):
    return await _telegram_retry(
        lambda: context.bot.send_message(
            chat_id=user_id,
            text=text,
            disable_web_page_preview=disable_web_page_preview,
        ),
        attempts=4,
        base_delay=1.0,
    )


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_channels':
        await q.edit_message_text('📢 Channels / Groups', reply_markup=self.channels_menu())
        return True
    if a == 'a_channel_add':
        plan_cfg, _ = await effective_plan(self.seller_account(context))
        existing = len(await get_channels(owner))
        limit = int(plan_cfg.get('channel_limit', 1))
        if limit >= 0 and existing >= limit:
            await q.edit_message_text(await plan_limit_warning(self.seller_account(context)), reply_markup=self.limit_keyboard('a_channels'))
            return True
        context.user_data.clear()
        context.user_data['wait_channel'] = True
        await q.edit_message_text('📢 Connect Channel / Group\n\n✅ Channel\n• Child bot ko channel me Admin banao.\n• Channel se koi bhi message yahan FORWARD karo.\n\n✅ Private Group (Recommended)\n1. Child bot ko group me add karo.\n2. Bot ko Admin banao.\n3. Invite Users permission ON rakho.\n4. Usi group ke andar /connectgroup bhejo.\n\nBot group automatically detect karke save karega aur invite-link permission test karega.\n\n🔄 Agar auto detect na ho:\n• Group se koi message yahan FORWARD karo.\n\n⚠️ Sirf last option:\n-100xxxxxxxxxx | Group Name', reply_markup=self.back('a_channels'))
        return True
    if a == 'a_channel_list':
        channels = await get_channels(owner)
        lines = ['📋 Channels / Groups\n', 'Choose which connected chats should receive automatic invite links after successful automatic payment verification.\n', '✅ Enabled: invite link will be sent\n❌ Disabled: invite link will be skipped']
        kb = []
        for ch in channels:
            enabled = ch.get('auto_invite_enabled', True) is not False
            status = '✅ Enabled' if enabled else '❌ Disabled'
            title = ch.get('title', 'Chat')
            lines.append(f"• {title}\n  {ch.get('chat_id')}\n  Auto Invite: {status}")
            kb.append([InlineKeyboardButton(f"{('✅' if enabled else '❌')} {title[:24]}", callback_data=f"a_channel_autoinvite_{ch['chat_id']}"), InlineKeyboardButton('🗑 Remove', callback_data=f"a_channel_del_{ch['chat_id']}")])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_channels')])
        await q.edit_message_text('\n\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_channel_autoinvite_'):
        chat_id = int(a.replace('a_channel_autoinvite_', '', 1))
        channels = await get_channels(owner)
        channel = next((item for item in channels if int(item.get('chat_id')) == chat_id), None)
        if not channel:
            await q.answer('Channel or group not found.', show_alert=True)
            return True
        current = channel.get('auto_invite_enabled', True) is not False
        await set_channel_auto_invite(owner, chat_id, not current)
        channels = await get_channels(owner)
        lines = ['📋 Channels / Groups\n', 'Choose which connected chats should receive automatic invite links after successful automatic payment verification.\n', '✅ Enabled: invite link will be sent\n❌ Disabled: invite link will be skipped']
        kb = []
        for ch in channels:
            enabled = ch.get('auto_invite_enabled', True) is not False
            status = '✅ Enabled' if enabled else '❌ Disabled'
            title = ch.get('title', 'Chat')
            lines.append(f"• {title}\n  {ch.get('chat_id')}\n  Auto Invite: {status}")
            kb.append([InlineKeyboardButton(f"{('✅' if enabled else '❌')} {title[:24]}", callback_data=f"a_channel_autoinvite_{ch['chat_id']}"), InlineKeyboardButton('🗑 Remove', callback_data=f"a_channel_del_{ch['chat_id']}")])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_channels')])
        await q.edit_message_text('\n\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a == 'a_channel_resend':
        channels = await get_channels(owner)
        if not channels:
            await q.edit_message_text('❌ Pehle kam se kam ek channel/group add karo.', reply_markup=self.channels_menu())
            return True
        active_count = len(await active_subscriptions(owner, limit=None))
        await q.edit_message_text(f'🔗 Group/Channel Invite Link Resend\n\nActive subscribers found: {active_count}\nChannels/Groups: {len(channels)}\n\nFresh invite links sabhi active subscribers ko bheje jayenge. Expired users ko message nahi jayega.\n\nContinue?', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Yes, Resend', callback_data='a_channel_resend_yes')], [InlineKeyboardButton('❌ No', callback_data='a_channels')]]))
        return True
    if a == 'a_channel_resend_yes':
        await q.edit_message_text('⏳ Invite links resend ho rahe hain...')
        channels = [channel for channel in await get_channels(owner) if channel.get('auto_invite_enabled', True) is not False]
        subscriptions = await active_subscriptions(owner, limit=None)
        sent = failed = invite_failed = 0
        now = datetime.now(timezone.utc)
        logger.info('Starting active-subscriber invite resend owner=%s active=%s channels=%s', owner, len(subscriptions), len(channels))
        for sub in subscriptions:
            user_id = int(sub['user_id'])
            expiry = sub.get('expiry_date')
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            remaining = expiry - now if expiry else None
            if not remaining or remaining.total_seconds() <= 0:
                continue
            days = remaining.days
            hours = remaining.seconds // 3600
            minutes = remaining.seconds % 3600 // 60
            link_lines = []
            for ch in channels:
                try:
                    invite = await _create_invite_with_retry(context, ch['chat_id'])
                    await save_invite(owner, user_id, ch['chat_id'], invite.invite_link)
                    link_lines.append(f"📢 {ch.get('title', 'Premium Channel')}\n{invite.invite_link}")
                except Exception as exc:
                    invite_failed += 1
                    logger.warning('Invite create failed after retries owner=%s chat=%s user=%s: %s', owner, ch.get('chat_id'), user_id, exc)
            if not link_lines:
                failed += 1
                await save_failed_delivery(owner, user_id, 'invite_resend', {'channels': [c.get('chat_id') for c in channels], 'reason': 'invite_creation_failed'}, 'No invite link could be created after retries')
                await asyncio.sleep(0.75)
                continue
            try:
                await _send_message_with_retry(context, user_id, f'📢 Channel/Group Invite Links Updated\n\nYour subscription is still active.\n\n⏱ Remaining: {days}d {hours}h {minutes}m\n\nJoin using the fresh invite link(s):\n\n' + '\n\n'.join(link_lines), disable_web_page_preview=True)
                sent += 1
            except Exception as exc:
                failed += 1
                await save_failed_delivery(owner, user_id, 'invite_resend', {'channels': [c.get('chat_id') for c in channels]}, str(exc))
                logger.warning('Invite resend failed owner=%s user=%s: %s', owner, user_id, exc)
            await asyncio.sleep(0.75)
        logger.info('Completed active-subscriber invite resend owner=%s active=%s sent=%s failed=%s invite_failed=%s', owner, len(subscriptions), sent, failed, invite_failed)
        await q.edit_message_text(f'✅ Invite Link Resend Completed\n\nActive subscribers: {len(subscriptions)}\nSuccessfully sent: {sent}\nFailed/blocked users: {failed}\nInvite creation failures: {invite_failed}\n\nExpired users ko message nahi bheja gaya.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔁 Retry Failed Users', callback_data='a_retry_failed')], [InlineKeyboardButton('⬅ Back', callback_data='a_channels')]]))
        return True
    if a == 'a_retry_failed':
        failed_docs = await get_failed_deliveries(owner, 'invite_resend')
        sent = still_failed = skipped = 0
        channels = [channel for channel in await get_channels(owner) if channel.get('auto_invite_enabled', True) is not False]
        for item in failed_docs:
            claimed = await claim_failed_delivery(item['_id'], owner, stale_after_seconds=600)
            if not claimed:
                skipped += 1
                continue
            uid = int(claimed.get('user_id'))
            try:
                links = []
                for ch in channels:
                    invite = await _create_invite_with_retry(context, ch['chat_id'])
                    await save_invite(owner, uid, ch['chat_id'], invite.invite_link)
                    links.append(f"{ch.get('title', 'Channel')}: {invite.invite_link}")
                await _send_message_with_retry(context, uid, '🔁 Fresh invite link(s):\n\n' + '\n'.join(links))
                resolved = await resolve_failed_delivery(claimed['_id'])
                if resolved:
                    sent += 1
                else:
                    still_failed += 1
                    logger.warning('Failed delivery retry sent but could not finalize owner_id=%s user_id=%s delivery_id=%s', owner, uid, claimed['_id'])
            except Exception as exc:
                still_failed += 1
                logger.exception('Failed delivery retry failed owner_id=%s user_id=%s delivery_id=%s', owner, uid, claimed['_id'])
                try:
                    await release_failed_delivery_claim(claimed['_id'], str(exc))
                except Exception:
                    logger.exception('Failed delivery claim release failed owner_id=%s user_id=%s delivery_id=%s', owner, uid, claimed['_id'])
            await asyncio.sleep(0.75)
        await q.edit_message_text(f'🔁 Retry completed\n\nSent: {sent}\nStill failed: {still_failed}\nAlready processing: {skipped}', reply_markup=self.admin_menu())
        return True
    if a.startswith('a_channel_del_'):
        await remove_channel(owner, int(a.replace('a_channel_del_', '')))
        await q.edit_message_text('✅ Removed', reply_markup=self.channels_menu())
        return True
    return False
