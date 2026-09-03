"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_referral_unlock':
        settings = await get_seller_settings(owner)
        channels = await get_channels(owner)
        await q.edit_message_text(self.referral_unlock_text(settings), reply_markup=self.referral_unlock_menu(settings, channels))
        return True
    if a == 'a_referral_unlock_toggle':
        settings = await get_seller_settings(owner)
        new_value = not bool(settings.get('referral_unlock_enabled', False))
        if new_value and (not settings.get('referral_unlock_target_chat_id')):
            await q.answer('Select a destination first.', show_alert=True)
            return True
        await set_seller_setting(owner, 'referral_unlock_enabled', new_value)
        settings = await get_seller_settings(owner)
        channels = await get_channels(owner)
        await q.edit_message_text(self.referral_unlock_text(settings), reply_markup=self.referral_unlock_menu(settings, channels))
        return True
    if a == 'a_referral_unlock_required':
        context.user_data.clear()
        context.user_data['wait_referral_unlock_required'] = True
        await q.edit_message_text('👥 Set Required Successful Referrals\n\nSend a whole number from 1 to 100.\n\nExample: 3\n\nOnly successful referrals are counted. Opening or sharing the link alone does not increase progress.', reply_markup=self.back('a_referral_unlock'))
        return True
    if a == 'a_referral_unlock_count_mode':
        settings = await get_seller_settings(owner)
        current = settings.get('referral_unlock_count_mode', 'subscription')
        new_mode = 'start' if current != 'start' else 'subscription'
        await set_seller_setting(owner, 'referral_unlock_count_mode', new_mode)
        settings = await get_seller_settings(owner)
        channels = await get_channels(owner)
        await q.edit_message_text(self.referral_unlock_text(settings), reply_markup=self.referral_unlock_menu(settings, channels))
        return True
    if a == 'a_referral_unlock_duration':
        context.user_data.clear()
        context.user_data['wait_referral_unlock_duration'] = True
        await q.edit_message_text('📅 Set Access Duration\n\nSend the number of days the unlocked group or channel access should remain active.\n\nAllowed range: 1 to 3650 days\nExample: 30', reply_markup=self.back('a_referral_unlock'))
        return True
    if a == 'a_referral_unlock_destination':
        channels = await get_channels(owner)
        if not channels:
            await q.edit_message_text('❌ No connected group or channel found.\n\nConnect a destination first from Channels / Groups, then return here.', reply_markup=self.back('a_referral_unlock'))
            return True
        await q.edit_message_text('📢 Select Unlock Destination\n\nChoose the private group or channel whose invite link will be given after the user completes the referral target.\n\nThe clone bot must be an administrator and must have permission to create invite links.', reply_markup=self.referral_unlock_channels_menu(channels))
        return True
    if a.startswith('a_referral_unlock_chat_'):
        chat_id = int(a.replace('a_referral_unlock_chat_', '', 1))
        channels = await get_channels(owner)
        selected = next((item for item in channels if int(item.get('chat_id')) == chat_id), None)
        if not selected:
            await q.answer('Destination not found.', show_alert=True)
            return True
        await set_seller_setting(owner, 'referral_unlock_target_chat_id', chat_id)
        await set_seller_setting(owner, 'referral_unlock_target_title', selected.get('title') or str(chat_id))
        settings = await get_seller_settings(owner)
        await q.edit_message_text(self.referral_unlock_text(settings), reply_markup=self.referral_unlock_menu(settings, channels))
        return True
    if a == 'a_seller_referral':
        data = await seller_referral_stats(owner)
        link = f'https://t.me/{MAIN_BOT_USERNAME}?start=refseller_{owner}'
        await q.edit_message_text(f"🤝 Seller Referral Program\n\n👥 Sellers joined: {data['total']}\n🎁 Rewards received: {data['rewarded']}\n\nShare this link with new sellers:\n{link}\n\nThe owner controls reward days and reward plan from Owner Dashboard → Subscription Management.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📤 Share Referral Link', url=f'https://t.me/share/url?url={link}')], [InlineKeyboardButton('⬅ Back', callback_data='a_home')]]), disable_web_page_preview=True)
        return True
    return False
