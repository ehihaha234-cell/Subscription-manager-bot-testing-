"""Plan Group based User Management callbacks."""

from handlers.common.clone_context import *
from uuid import uuid4
from datetime import datetime, timezone


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


# --- Point 1: restore access after admin extension ---
async def _restore_plan_group_access_after_admin_extend(bot, owner, user_id, group_id, expiry_date=None):
    """Send fresh Plan Group invite links after Give/Extend.

    This is intentionally used for all three states: no previous subscription,
    active subscription, and expired subscription. Expired/kicked users are
    unbanned first so the generated invite can be used normally.
    """
    group = await get_plan_group(owner, str(group_id))
    if not group:
        return {"sent": 0, "links": []}

    # Extend existing recorded links to the newly calculated subscription expiry
    # before creating fresh links. The expiry worker can then revoke all links
    # for this subscription at the same final expiry time.
    if expiry_date is not None:
        try:
            from database.subscription_invites import update_active_invite_expiry
            await update_active_invite_expiry(owner, user_id, group_id, expiry_date)
        except Exception:
            logger.exception(
                "Failed updating existing invite expiry owner=%s user=%s group=%s",
                owner, user_id, group_id,
            )

    links = []
    # Resolve Plan Group targets against the seller's currently connected chats.
    # A stale/mismatched target chat_id can make Telegram return "Chat not found".
    try:
        connected_channels = await get_channels(int(owner))
    except Exception:
        logger.exception("Could not load connected chats for admin invite owner=%s group=%s", owner, group_id)
        connected_channels = []

    connected_by_id = {}
    for channel in connected_channels:
        try:
            connected_by_id[int(channel.get("chat_id"))] = channel
        except (TypeError, ValueError):
            continue

    used_chat_ids = set()
    for target in (group.get("targets") or []):
        try:
            configured_chat_id = int(target.get("chat_id"))
        except (TypeError, ValueError, AttributeError):
            continue

        channel = connected_by_id.get(configured_chat_id)
        # If the saved Plan Group target ID is stale, match its saved title
        # to an active connected channel. Never guess a chat from group_id.
        if channel is None:
            target_title = str((target or {}).get("title") or "").strip().casefold()
            if target_title:
                matches = [
                    ch for ch in connected_channels
                    if str(ch.get("title") or "").strip().casefold() == target_title
                ]
                if len(matches) == 1:
                    channel = matches[0]

        if channel is None:
            logger.error(
                "Admin invite target is not connected owner=%s user=%s group=%s configured_chat=%s title=%s",
                owner, user_id, group_id, configured_chat_id, (target or {}).get("title"),
            )
            continue

        try:
            chat_id = int(channel.get("chat_id"))
        except (TypeError, ValueError):
            continue
        if chat_id in used_chat_ids:
            continue
        used_chat_ids.add(chat_id)
        title = str(channel.get("title") or (target or {}).get("title") or chat_id)

        try:
            # Validate that this bot can actually resolve the target chat before
            # attempting to create an invite. This gives a clear error for stale IDs.
            await bot.get_chat(chat_id)
            me = await bot.get_me()
            bot_member = await bot.get_chat_member(chat_id, int(me.id))
            bot_status = getattr(bot_member, "status", "")
            can_invite = bool(getattr(bot_member, "can_invite_users", False))
            if not (
                bot_status == "creator"
                or (bot_status == "administrator" and can_invite)
            ):
                logger.error(
                    "Admin invite skipped: bot lacks invite permission owner=%s user=%s chat=%s status=%s can_invite=%s",
                    owner, user_id, chat_id, bot_status, can_invite,
                )
                continue
        except Exception:
            logger.exception(
                "Admin invite target chat validation failed owner=%s user=%s group=%s chat=%s",
                owner, user_id, group_id, chat_id,
            )
            continue

        try:
            member = await bot.get_chat_member(chat_id, int(user_id))
            if getattr(member, "status", "") == "kicked":
                await bot.unban_chat_member(chat_id, int(user_id), only_if_banned=True)
        except Exception:
            pass

        invite = None
        try:
            telegram_expire_date = None
            if expiry_date is not None:
                normalized_expiry = expiry_date
                if isinstance(normalized_expiry, str):
                    normalized_expiry = datetime.fromisoformat(normalized_expiry.replace("Z", "+00:00"))
                if getattr(normalized_expiry, "tzinfo", None) is None:
                    normalized_expiry = normalized_expiry.replace(tzinfo=timezone.utc)
                normalized_expiry = normalized_expiry.astimezone(timezone.utc)
                if normalized_expiry <= datetime.now(timezone.utc):
                    raise ValueError("Subscription expiry is not in the future")
                telegram_expire_date = normalized_expiry
            else:
                raise ValueError("Subscription expiry missing; cannot create a timed invite")

            invite = await bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,
                expire_date=telegram_expire_date,
                name=f"Subscription access {user_id}",
            )

            from database.subscription_invites import record_subscription_invite
            saved = await record_subscription_invite(
                owner, user_id, chat_id, invite.invite_link,
                expiry_date, group_id=group_id,
            )
            if not saved:
                logger.error(
                    "Invite expiry record was not saved owner=%s user=%s chat=%s group=%s",
                    owner, user_id, chat_id, group_id,
                )
                try:
                    await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite.invite_link)
                except Exception:
                    logger.exception("Failed revoking untracked admin invite owner=%s user=%s chat=%s", owner, user_id, chat_id)
                continue

            links.append(f"📢 {title}\\n{invite.invite_link}")
        except Exception:
            logger.exception(
                "Failed creating Plan Group access link owner=%s user=%s group=%s chat=%s",
                owner, user_id, group_id, chat_id,
            )

    if links:
        try:
            await bot.send_message(
                int(user_id),
                "🎉 Your subscription has been updated.\n\n"
                "🔗 Fresh private invite link(s):\n\n"
                + "\n\n".join(links),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "Failed sending Plan Group access links owner=%s user=%s",
                owner, user_id,
            )
    return {"sent": len(links), "links": links}


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

        # Give / Extend uses one unified operation:
        # - no previous subscription -> create it (Give)
        # - active subscription -> add duration to current expiry (Extend)
        # - expired subscription -> reactivate from now and add duration
        # The Plan Group selected above is always the target scope.
        group = await get_plan_group(owner, gid)
        targets = (group or {}).get('targets') or []
        target_ids = []
        for item in targets:
            try:
                target_ids.append(int(item.get('chat_id')))
            except (TypeError, ValueError, AttributeError):
                pass
        if not target_ids:
            target_ids = [int(x) for x in ((group or {}).get('chat_ids') or [])]
        if not target_ids:
            await q.edit_message_text(
                '❌ This Plan Group has no connected Group/Channel.',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
            return True

        previous = await get_plan_group_subscription(owner, user_id, gid)
        previous_expiry = (previous or {}).get('expiry_date')
        if previous_expiry and getattr(previous_expiry, 'tzinfo', None) is None:
            previous_expiry = previous_expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        was_active = bool(
            previous
            and previous.get('active')
            and previous_expiry
            and previous_expiry > now
        )

        result = await fulfill_plan_group_subscription(
            owner, user_id,
            f'admin_extend:{owner}:{user_id}:{gid}:{uuid4().hex}',
            gid,
            (previous or {}).get('plan') or 'Admin Subscription',
            duration_minutes,
            amount=0,
            duration_text=duration_text,
            target_chat_ids=target_ids,
        )

        # All three cases must deliver fresh invite links: new subscription,
        # active extension, and expired-subscription reactivation.
        await _restore_plan_group_access_after_admin_extend(context.bot, owner, user_id, gid, result.get("expiry_date"))
        context.user_data.clear()

        status_text = 'extended' if was_active else ('reactivated' if previous else 'created')
        try:
            await q.edit_message_text(
                '✅ Subscription updated successfully.\n\n'
                f'📦 Plan Group: {await _group_label(owner, result.get("subscription") or {"group_id": gid, "target_chat_ids": target_ids})}\n'
                f'📅 Duration added: {duration_text}\n'
                f'🔄 Status: {status_text.title()}\n'
                f'⏳ New expiry: {self.format_dt(result.get("expiry_date"), await self.seller_timezone(owner))}',
                reply_markup=self.back(f'a_user_view_{user_id}'),
            )
        except Exception:
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
        if result.get('removed'):
            try:
                from database.subscription_invites import revoke_subscription_invites
                await revoke_subscription_invites(context.bot, owner, user_id=user_id, group_id=gid)
            except Exception:
                logger.exception("Invite revocation on admin Plan Group removal failed owner=%s user=%s group=%s", owner, user_id, gid)
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

        # A removed Plan Group frees capacity immediately. Resume previously
        # blocked plan selections for this seller/clone without waiting for a
        # seller-plan upgrade. The recovery routine re-checks capacity per item.
        try:
            from services.bot_manager import bot_manager
            seller_account_id = int(
                context.application.bot_data.get('seller_account_id') or owner
            )
            await bot_manager.resume_pending_limited_purchases(seller_account_id)
        except Exception:
            logger.exception(
                'Could not resume limited plan selections after admin removal owner=%s user=%s group=%s',
                owner, user_id, gid,
            )

        await self.show_user_details(q, owner, user_id)
        return True

    if a.startswith('a_user_remove_'):
        user_id = int(a.replace('a_user_remove_', ''))
        rows = await get_user_plan_group_subscriptions(owner, user_id)
        now_utc = datetime.now(timezone.utc)
        active_rows = []
        for sub in rows:
            if not sub.get('active'):
                continue
            expiry = sub.get('expiry_date')
            if expiry:
                # MongoDB may return a naive datetime while `now_utc` is aware.
                # Treat naive stored datetimes as UTC before comparing.
                if isinstance(expiry, datetime) and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                elif isinstance(expiry, datetime):
                    expiry = expiry.astimezone(timezone.utc)
                if expiry <= now_utc:
                    continue
            active_rows.append(sub)
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
