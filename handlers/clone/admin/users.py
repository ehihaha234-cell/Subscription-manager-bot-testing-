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
        groups = await get_plan_groups(owner)
        rows = []
        for group in groups:
            gid = str(group.get('group_id'))
            targets = group.get('targets') or []
            label = ', '.join(str(x.get('title') or x.get('chat_id')) for x in targets) or gid
            sub = await get_plan_group_subscription(owner, user_id, gid)
            if sub and sub.get('active'):
                rows.append([InlineKeyboardButton(f'📦 {label[:48]}', callback_data=f'a_user_pg_manage_{user_id}_{gid}')])
        rows.append([InlineKeyboardButton('⬅ Back', callback_data=f'a_user_view_{user_id}')])
        text = (
            '🎁 Give / Extend Subscription\n\n'
            'Select the subscription you want to extend:'
            if rows[:-1] else
            '🎁 Give / Extend Subscription\n\n'
            'No active Plan Group subscription found.'
        )
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return True
    if a.startswith('a_user_pg_manage_'):
        parts = a.split('_', 5)
        if len(parts) != 6:
            await q.edit_message_text('❌ Invalid action.')
            return True
        user_id = int(parts[4])
        gid = parts[5]
        sub = await get_plan_group_subscription(owner, user_id, gid)
        group = await get_plan_group(owner, gid)
        if not sub or not group:
            await q.edit_message_text('❌ Plan Group subscription not found.', reply_markup=self.back(f'a_user_view_{user_id}'))
            return True
        context.user_data.clear()
        context.user_data['wait_user_plan_group_duration'] = {'user_id': user_id, 'group_id': gid}
        targets = group.get('targets') or []
        label = ', '.join(str(x.get('title') or x.get('chat_id')) for x in targets) or gid
        expiry = sub.get('expiry_date')
        await q.edit_message_text(
            f'🎁 Extend Plan Group Subscription\n\n📦 Group/Channel: {label}\n📅 Current Expiry: {self.format_dt(expiry)}\n\n'
            'Send a custom duration:\n30m, 12h, 7d, 3mo or 1y.\n\n'
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
        delivery = await self.deliver_subscription_access(owner, user_id, {'target_chat_ids': [int(x) for x in (plan.get('target_chat_ids') or [])]})
        try:
            await context.bot.send_message(user_id, f"🎉 Subscription activated/extended by admin.\nPlan: {plan['name']}\nDuration added: {plan['duration_text']}\n\nNew invite links sent: {delivery.get('sent', 0)}\nAlready joined: {delivery.get('already_member', 0)}")
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True
    if a.startswith('a_user_remove_'):
        user_id = int(a.replace('a_user_remove_', ''))
        context.user_data.clear()
        groups = await get_plan_groups(owner)
        rows = []
        for group in groups:
            gid = str(group.get('group_id'))
            targets = group.get('targets') or []
            label = ', '.join(str(x.get('title') or x.get('chat_id')) for x in targets) or gid
            sub = await get_plan_group_subscription(owner, user_id, gid)
            if sub and sub.get('active'):
                rows.append([InlineKeyboardButton(f'❌ {label[:48]}', callback_data=f'a_user_pg_remove_{user_id}_{gid}')])
        rows.append([InlineKeyboardButton('⬅ Back', callback_data=f'a_user_view_{user_id}')])
        text = (
            '❌ Remove Subscription\n\n'
            'Select the subscription you want to remove:'
            if rows[:-1] else
            '❌ Remove Subscription\n\n'
            'No active Plan Group subscription found.'
        )
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return True

    if a.startswith('a_user_pg_remove_'):
        parts = a.split('_', 5)
        if len(parts) != 6:
            await q.edit_message_text('❌ Invalid action.')
            return True
        user_id = int(parts[4])
        gid = parts[5]
        sub = await get_plan_group_subscription(owner, user_id, gid)
        group = await get_plan_group(owner, gid)
        if not sub or not sub.get('active') or not group:
            await q.edit_message_text(
                '❌ Plan Group subscription not found.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        result = await remove_plan_group_subscription(owner, user_id, gid)
        if not result:
            await q.edit_message_text(
                '❌ Plan Group subscription is already inactive.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        target_ids = []
        for value in result.get('target_chat_ids') or group.get('chat_ids') or []:
            try:
                target_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        target_ids = list(dict.fromkeys(target_ids))

        # Remove only from chats owned by the selected Plan Group. If another
        # active Plan Group still covers the same chat, keep the user's access.
        removed_chat_ids = []
        for chat_id in target_ids:
            try:
                if await active_plan_group_subscriptions_for_chat(owner, user_id, chat_id):
                    continue
                member = await context.bot.get_chat_member(chat_id, user_id)
                if getattr(member, 'status', '') in {'creator', 'administrator'}:
                    continue
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                removed_chat_ids.append(chat_id)
            except TelegramError as exc:
                logger.warning(
                    'Admin Plan Group removal failed owner=%s user=%s group=%s chat=%s: %s',
                    owner, user_id, gid, chat_id, exc,
                )
            except Exception:
                logger.exception(
                    'Unexpected Admin Plan Group removal failure owner=%s user=%s group=%s chat=%s',
                    owner, user_id, gid, chat_id,
                )

        targets = group.get('targets') or []
        target_names = [str(x.get('title') or x.get('chat_id')) for x in targets]
        try:
            lines = [
                '❌ Your subscription was removed by admin.',
                '',
                f"📦 Plan: {sub.get('plan') or 'Plan Group'}",
                '🔊 Group/Channel:',
            ]
            lines.extend(f'• {name}' for name in target_names)
            await context.bot.send_message(user_id, '\n'.join(lines))
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
