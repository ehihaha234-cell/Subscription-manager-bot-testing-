"""Plan Group based User Management callbacks."""

from handlers.common.clone_context import *


def _duration_minutes(value: str):
    value = str(value or "").strip().lower()
    if value.endswith("mo"):
        amount = int(value[:-2]); return amount * 30 * 1440
    if value.endswith("y"):
        amount = int(value[:-1]); return amount * 365 * 1440
    if value.endswith("m"):
        amount = int(value[:-1]); return amount
    if value.endswith("h"):
        amount = int(value[:-1]); return amount * 60
    if value.endswith("d"):
        amount = int(value[:-1]); return amount * 1440
    raise ValueError


async def _group_label(owner, sub):
    group = await get_plan_group(owner, str(sub.get("group_id") or ""))
    if group:
        targets = group.get("targets") or []
        names = [str(x.get("title") or x.get("chat_id")) for x in targets]
        if names:
            return ", ".join(names)
    targets = sub.get("target_chat_ids") or []
    return ", ".join(str(x) for x in targets) or str(sub.get("group_id") or "Plan Group")



# --- Point 14: callback target validation ---
async def _callback_user_allowed(owner, user_id):
    """Ensure a crafted User Management callback can only target a user
    belonging to the current seller/clone owner scope.
    """
    try:
        return bool(await get_user(owner, int(user_id)))
    except (TypeError, ValueError):
        return False


async def _callback_group_allowed(owner, group_id):
    """Ensure a crafted callback cannot select another owner's Plan Group."""
    try:
        return bool(await get_plan_group(owner, str(group_id)))
    except Exception:
        return False


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_users':
        context.user_data.clear()
        context.user_data['wait_user_search'] = True
        await q.edit_message_text('👥 User Management\n\nSend User ID or @username to search.', reply_markup=self.back('a_home'))
        return True

    if a.startswith('a_user_view_'):
        try:
            user_id = int(a.replace('a_user_view_', ''))
        except (TypeError, ValueError):
            await q.answer('Invalid user.', show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True
        await self.show_user_details(q, owner, user_id)
        return True

    if a.startswith('a_user_manage_') or a.startswith('a_user_give_') or a.startswith('a_user_extend_') or a.startswith('a_user_custom_'):
        prefix = next(x for x in ('a_user_manage_', 'a_user_give_', 'a_user_extend_', 'a_user_custom_') if a.startswith(x))
        try:
            user_id = int(a.replace(prefix, ''))
        except (TypeError, ValueError):
            await q.answer('Invalid user.', show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True

        groups = await get_plan_groups(owner)
        if not groups:
            await q.edit_message_text(
                '🎁 Give / Extend Subscription\n\nNo Plan Group found. Create a Plan Group first.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        context.user_data.clear()
        kb = []
        for group in groups:
            gid = str(group.get('group_id') or '').strip()
            if not gid:
                continue
            targets = group.get('targets') or []
            names = [str(x.get('title') or x.get('chat_id')) for x in targets]
            label = 'Group/Channel' if not names else ', '.join(names)
            kb.append([InlineKeyboardButton(
                f'📦 {label[:48]}',
                callback_data=f'a_user_group_select_{user_id}_{gid}',
            )])

        if not kb:
            await q.edit_message_text(
                '🎁 Give / Extend Subscription\n\nNo valid Plan Group found.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        kb.append([InlineKeyboardButton('⬅ Back', callback_data=f'a_user_view_{user_id}')])
        await q.edit_message_text(
            '🎁 Give / Extend Subscription\n\nSelect a Plan Group:',
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return True

    if a.startswith('a_user_group_select_'):
        payload = a.replace('a_user_group_select_', '', 1)
        try:
            user_text, gid = payload.split('_', 1)
            user_id = int(user_text)
        except (TypeError, ValueError):
            await q.answer('Invalid Plan Group selection.', show_alert=True)
            return True

        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True

        group = await get_plan_group(owner, gid)
        if not group:
            await q.edit_message_text(
                '❌ Plan Group not found or is no longer active.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        targets = group.get('targets') or []
        names = [str(x.get('title') or x.get('chat_id')) for x in targets]
        label = ', '.join(names) or '-'

        context.user_data.clear()
        context.user_data['wait_user_group_duration'] = user_id
        context.user_data['wait_user_group_duration_gid'] = gid
        await q.edit_message_text(
            '🎁 Extend Subscription\n\n'
            f'📦 Group/Channel: {label}\n\n'
            'Send a custom duration:\n'
            '30m, 12h, 7d, 3mo or 1y.\n\n'
            'Existing active validity will be preserved and the new duration will be added.',
            reply_markup=self.back(f'a_user_view_{user_id}'),
        )
        return True

    if a.startswith('a_user_group_extend_'):
        payload = a.replace("a_user_group_extend_", "", 1)
        try:
            user_text, gid = payload.split("_", 1)
            user_id = int(user_text)
        except (TypeError, ValueError):
            await q.answer("Invalid subscription selection.", show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True
        if not await _callback_group_allowed(owner, gid):
            await q.answer('❌ Plan Group is not available in this account.', show_alert=True)
            return True

        duration_text = str(context.user_data.get('user_custom_duration_text') or '').strip().lower()
        try:
            duration_minutes = _duration_minutes(duration_text)
            if duration_minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await q.answer('Duration expired. Start Extend Subscription again.', show_alert=True)
            return True

        sub = await get_plan_group_subscription(owner, user_id, gid)
        if not sub:
            await q.edit_message_text('❌ Plan Group subscription not found.', reply_markup=self.back(f'a_user_view_{user_id}'))
            return True

        target_ids = [int(x) for x in (sub.get('target_chat_ids') or [])]
        result = await fulfill_plan_group_subscription(
            owner, user_id,
            f'admin_extend:{owner}:{user_id}:{gid}:{uuid4().hex}',
            gid,
            sub.get('plan') or 'Admin Extension',
            duration_minutes,
            amount=0,
            duration_text=duration_text,
            target_chat_ids=target_ids,
        )
        context.user_data.clear()
        try:
            await context.bot.send_message(
                user_id,
                '🎉 Plan Group subscription extended by admin.\n'
                f"Plan: {sub.get('plan') or 'Plan'}\n"
                f'Duration added: {duration_text}\n'
                f'New expiry: {self.format_dt(result.get("expiry_date"), await self.seller_timezone(owner))}',
            )
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True

    if a.startswith('a_user_group_remove_'):
        payload = a.replace("a_user_group_remove_", "", 1)
        try:
            user_text, gid = payload.split("_", 1)
            user_id = int(user_text)
        except (TypeError, ValueError):
            await q.answer("Invalid subscription selection.", show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True
        if not await _callback_group_allowed(owner, gid):
            await q.answer('❌ Plan Group is not available in this account.', show_alert=True)
            return True
        result = await remove_plan_group_subscription(owner, user_id, gid)
        if not result.get('removed'):
            await q.edit_message_text('❌ Plan Group subscription is no longer active.', reply_markup=self.back(f'a_user_view_{user_id}'))
            return True

        for chat_id in result.get('target_chat_ids', []):
            try:
                member = await context.bot.get_chat_member(chat_id, user_id)
                if getattr(member, 'status', '') in {'creator', 'administrator'}:
                    continue
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            except TelegramError as exc:
                logger.warning('Plan Group removal failed owner=%s user=%s chat=%s: %s', owner, user_id, chat_id, exc)
            except Exception:
                logger.exception('Unexpected Plan Group removal failure owner=%s user=%s chat=%s', owner, user_id, chat_id)

        try:
            await context.bot.send_message(user_id, '❌ Your Plan Group subscription was removed by admin.')
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True

    if a.startswith('a_user_remove_'):
        user_id = int(a.replace('a_user_remove_', ''))
        rows = await get_user_plan_group_subscriptions(owner, user_id)
        active_rows = [x for x in rows if x.get('active') and (not x.get('expiry_date') or x.get('expiry_date') > datetime.now(timezone.utc))]
        if not active_rows:
            await q.edit_message_text('❌ No active Plan Group subscription found.', reply_markup=self.back(f'a_user_view_{user_id}'))
            return True
        kb = []
        for sub in active_rows:
            gid = str(sub.get('group_id') or '')
            if not gid:
                continue
            kb.append([InlineKeyboardButton(
                f"📦 {sub.get('plan') or 'Plan'} — {(await _group_label(owner, sub))[:40]}",
                callback_data=f'a_user_group_remove_{user_id}_{gid}',
            )])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data=f'a_user_view_{user_id}')])
        await q.edit_message_text(
            '❌ Remove Plan Group Subscription\n\nSelect the subscription to remove:',
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return True

    if a.startswith('a_user_apply_'):
        # Legacy plan buttons are intentionally no longer used by User Management.
        await q.answer('Please use the Plan Group subscription selector.', show_alert=True)
        return True

    if a.startswith('a_user_ban_'):
        try:
            user_id = int(a.replace('a_user_ban_', ''))
        except (TypeError, ValueError):
            await q.answer('Invalid user.', show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True
        context.user_data.clear()
        context.user_data['wait_user_ban_reason'] = user_id
        await q.edit_message_text('🚫 Send ban reason.', reply_markup=self.back(f'a_user_view_{user_id}'))
        return True

    if a.startswith('a_user_unban_'):
        try:
            user_id = int(a.replace('a_user_unban_', ''))
        except (TypeError, ValueError):
            await q.answer('Invalid user.', show_alert=True)
            return True
        if not await _callback_user_allowed(owner, user_id):
            await q.answer('❌ User is not available in this account.', show_alert=True)
            return True
        await set_user_ban(owner, user_id, False, '')
        try:
            await context.bot.send_message(user_id, '✅ You have been unbanned.')
        except Exception:
            pass
        await self.show_user_details(q, owner, user_id)
        return True

    return False
