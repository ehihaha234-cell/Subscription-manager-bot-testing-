"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_users':
        context.user_data.clear()
        context.user_data['wait_user_search'] = True
        await q.edit_message_text('👥 User Management\n\nSend User ID or @username to search.', reply_markup=self.back('a_home'))
        return True
    if a.startswith('a_user_view_'):
        await self.show_user_details(q, owner, int(a.replace('a_user_view_', '')))
        return True
    if a.startswith('a_user_manage_'):
        user_id = int(a.replace('a_user_manage_', ''))
        context.user_data.clear()
        context.user_data['wait_user_custom_duration'] = user_id
        await q.edit_message_text(
            '🎁 Give / Extend Clone Bot Subscription\n\n'
            'Send a custom duration:\n'
            '30m, 12h, 7d, 3mo or 1y.\n\n'
            'Existing active validity will be preserved and the new duration will be added.',
            reply_markup=self.back(f'a_user_view_{user_id}'),
        )
        return True
    # Backward-compatible routing for old inline buttons still visible in chat.
    if a.startswith('a_user_give_'):
        user_id = int(a.replace('a_user_give_', ''))
        context.user_data.clear()
        context.user_data['wait_user_custom_duration'] = user_id
        await q.edit_message_text(
            '🎁 Give / Extend Clone Bot Subscription\n\n'
            'Send a custom duration:\n'
            '30m, 12h, 7d, 3mo or 1y.\n\n'
            'Existing active validity will be preserved and the new duration will be added.',
            reply_markup=self.back(f'a_user_view_{user_id}'),
        )
        return True
    if a.startswith('a_user_extend_'):
        user_id = int(a.replace('a_user_extend_', ''))
        context.user_data.clear()
        context.user_data['wait_user_custom_duration'] = user_id
        await q.edit_message_text(
            '🎁 Give / Extend Clone Bot Subscription\n\n'
            'Send a custom duration:\n'
            '30m, 12h, 7d, 3mo or 1y.\n\n'
            'Existing active validity will be preserved and the new duration will be added.',
            reply_markup=self.back(f'a_user_view_{user_id}'),
        )
        return True
    if a.startswith('a_user_custom_'):
        user_id = int(a.replace('a_user_custom_', ''))
        context.user_data.clear()
        context.user_data['wait_user_custom_duration'] = user_id
        await q.edit_message_text(
            '🎁 Give / Extend Clone Bot Subscription\n\n'
            'Send a custom duration:\n'
            '30m, 12h, 7d, 3mo or 1y.\n\n'
            'Existing active validity will be preserved and the new duration will be added.',
            reply_markup=self.back(f'a_user_view_{user_id}'),
        )
        return True
    if a.startswith('a_user_apply_'):
        parts = a.split('_', 5)
        if len(parts) != 6:
            await q.edit_message_text('❌ Invalid action.')
            return True
        mode = parts[3]
        user_id = int(parts[4])
        plan_id = parts[5]
        plan = await get_plan(owner, plan_id)
        if not plan:
            await q.edit_message_text('❌ Plan not found.', reply_markup=self.back(f'a_user_view_{user_id}'))
            return True
        plan_cfg, _ = await effective_plan(self.seller_account(context))
        active_now = await active_subscriptions(owner)
        already_active = any((int(x.get('user_id')) == user_id for x in active_now))
        sub_limit = int(plan_cfg.get('active_subscriber_limit', 25))
        if not already_active and sub_limit >= 0 and (len(active_now) >= sub_limit):
            await q.edit_message_text(await plan_limit_warning(self.seller_account(context)), reply_markup=self.limit_keyboard(f'a_user_view_{user_id}'))
            return True
        await activate_subscription(owner, user_id, plan['name'], plan['duration_minutes'], amount=plan.get('price'), duration_text=plan.get('duration_text'))
        delivery = await self.deliver_subscription_access(owner, user_id)
        try:
            await context.bot.send_message(user_id, f"🎉 Subscription activated/extended by admin.\nPlan: {plan['name']}\nDuration added: {plan['duration_text']}\n\nNew invite links sent: {delivery.get('sent', 0)}\nAlready joined: {delivery.get('already_member', 0)}")
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True
    if a.startswith('a_user_remove_'):
        user_id = int(a.replace('a_user_remove_', ''))
        await remove_subscription(owner, user_id)
        try:
            await context.bot.send_message(user_id, '❌ Your subscription was removed by admin.')
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True
    if a.startswith('a_user_ban_'):
        user_id = int(a.replace('a_user_ban_', ''))
        context.user_data.clear()
        context.user_data['wait_user_ban_reason'] = user_id
        await q.edit_message_text('🚫 Send ban reason.', reply_markup=self.back(f'a_user_view_{user_id}'))
        return True
    if a.startswith('a_user_unban_'):
        user_id = int(a.replace('a_user_unban_', ''))
        await set_user_ban(owner, user_id, False, '')
        try:
            await context.bot.send_message(user_id, '✅ You have been unbanned.')
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True
    if a == 'a_stats':
        s = await stats(owner)
        settings = await get_seller_settings(owner)
        currency = settings.get('currency')
        text = (
            "📊 Statistics\n\n"
            f"👥 Total Users: {s.get('total_users', s.get('users', 0)):,}\n"
            f"🟢 Active Users (Today): {s.get('active_users_today', 0):,}\n"
            f"✅ Active Subscribers: {s.get('active_subscribers', s.get('active', 0)):,}\n"
            f"📦 Plans: {s.get('plans', 0):,}\n"
            f"📢 Channels / Groups: {s.get('channels', 0):,}\n"
            f"⏳ Pending Payments: {s.get('pending', 0):,}\n"
            f"💰 Today Revenue: {format_currency(currency, float(s.get('today_revenue', 0) or 0))}\n"
            f"💵 Total Revenue: {format_currency(currency, float(s.get('total_revenue', s.get('revenue', 0)) or 0))}"
        )
        await q.edit_message_text(text, reply_markup=self.back('a_home'))
        return True
    return False
