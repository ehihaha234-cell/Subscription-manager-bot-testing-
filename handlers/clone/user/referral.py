"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from datetime import timedelta
from handlers.common.feature_navigation import feature_back_callback


async def handle(self, update, context, q, owner, action):
    back_keyboard = self.back(feature_back_callback(context))
    if action == 'c_referral':
        me = await context.bot.get_me()
        settings = await get_seller_settings(owner)
        reward_days = int(settings.get('referral_reward_days', 7) or 7)
        total = await count_all_referrals(owner, q.from_user.id)
        successful = await count_successful_referrals(owner, q.from_user.id)
        referral_link = f'https://t.me/{me.username}?start=ref_{q.from_user.id}'
        share_url = 'https://t.me/share/url?url=' + referral_link + '&text=Join%20this%20subscription%20bot'
        text = f'🎁 Referral Program\n\n👥 Total Referrals: {total}\n✅ Successful Referrals: {successful}\n🎉 Reward: {reward_days} Free Days per successful referral.\n\n🔗 Your Referral Link:\n{referral_link}'
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('📤 Share Referral Link', url=share_url)], [InlineKeyboardButton('⬅ Back', callback_data='c_home')]])
        await self.safe_query_message(q, text, kb)
        return True
    if action == 'c_referral_unlock':
        settings = await get_seller_settings(owner)
        enabled = bool(settings.get('referral_unlock_enabled', False))
        required = max(1, int(settings.get('referral_unlock_required', 3) or 3))
        target_chat_id = settings.get('referral_unlock_target_chat_id')
        target_title = settings.get('referral_unlock_target_title') or 'Private Group'
        duration_days = max(1, int(settings.get('referral_unlock_duration_days', 30) or 30))
        count_mode = settings.get('referral_unlock_count_mode', 'subscription')
        counted = await count_all_referrals(owner, q.from_user.id) if count_mode == 'start' else await count_successful_referrals(owner, q.from_user.id)
        progress = min(counted, required)
        me = await context.bot.get_me()
        referral_link = f'https://t.me/{me.username}?start=ref_{q.from_user.id}'
        share_url = 'https://t.me/share/url?url=' + referral_link + '&text=Join%20this%20bot'
        if not enabled or not target_chat_id:
            await self.safe_query_message(q, '🔓 Referral Unlock is not available right now.\n\nPlease contact support.', InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data='c_home')]]))
            return True
        if counted < required:
            count_instruction = f'Invite {required} new user(s) with your referral link.\n\n' if count_mode == 'start' else f'Invite {required} user(s) who complete a subscription.\n\n'
            text = '🔓 Unlock Private Access\n\n' + count_instruction + f'Progress: {progress}/{required}\n\nYour unique referral link:\n{referral_link}\n\nAfter the required referrals are completed, open this button again to receive the private invite link.'
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('📤 Share Referral Link', url=share_url)], [InlineKeyboardButton('🔄 Check Progress', callback_data='c_referral_unlock')], [InlineKeyboardButton('⬅ Back', callback_data='c_home')]])
            await self.safe_query_message(q, text, kb)
            return True
        saved = await get_referral_unlock(owner, q.from_user.id)
        invite_link = (saved or {}).get('invite_link')
        if not invite_link:
            try:
                invite = await context.bot.create_chat_invite_link(chat_id=int(target_chat_id), member_limit=1, expire_date=datetime.now(timezone.utc) + timedelta(days=duration_days), name=f'Referral unlock {q.from_user.id}')
                invite_link = invite.invite_link
                await save_referral_unlock(owner, q.from_user.id, int(target_chat_id), invite_link, duration_days)
            except Exception:
                logger.exception('Referral unlock invite failed owner=%s user=%s', owner, q.from_user.id)
                await self.safe_query_message(q, '❌ The private invite link could not be created.\n\nPlease contact support.', InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data='c_home')]]))
                return True
        await self.safe_query_message(q, f'🎉 Referral Target Completed!\n\nProgress: {counted}/{required}\nDestination: {target_title}\n\nUse your private one-time invite link below.', InlineKeyboardMarkup([[InlineKeyboardButton('🔓 Join Now', url=invite_link)], [InlineKeyboardButton('⬅ Back', callback_data='c_home')]]))
        return True
    return False
