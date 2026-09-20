"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import feature_back_callback


async def handle(self, update, context, q, owner, action):
    back_keyboard = self.back(feature_back_callback(context))
    if action == 'c_profile':
        try:
            # Profile can contain multiple independent Plan Group subscriptions.
            # Fetch the legacy clone-wide subscription as a fallback, while
            # Plan Group subscriptions are always displayed separately.
            timezone_task = asyncio.create_task(self.seller_timezone(owner))
            user_task = asyncio.create_task(get_user(owner, q.from_user.id))
            legacy_sub_task = asyncio.create_task(get_subscription(owner, q.from_user.id))
            group_subs_task = asyncio.create_task(
                get_user_plan_group_subscriptions(owner, q.from_user.id)
            )
            plan_groups_task = asyncio.create_task(get_plan_groups(owner))
            settings_task = asyncio.create_task(get_seller_settings(owner))
            me_task = asyncio.create_task(context.bot.get_me())
            referrals_task = asyncio.create_task(count_all_referrals(owner, q.from_user.id))
            successful_referrals_task = asyncio.create_task(
                count_successful_referrals(owner, q.from_user.id)
            )

            (
                timezone_name,
                user_record,
                legacy_sub,
                group_subs,
                plan_groups,
                seller_settings,
                me,
                total_referrals,
                successful_referrals,
            ) = await asyncio.gather(
                timezone_task,
                user_task,
                legacy_sub_task,
                group_subs_task,
                plan_groups_task,
                settings_task,
                me_task,
                referrals_task,
                successful_referrals_task,
            )
            user_record = user_record or {}
            group_subs = group_subs or []
            plan_groups = plan_groups or []
            plan_groups_by_id = {
                str(group.get('group_id') or '').strip(): group
                for group in plan_groups
                if group.get('group_id')
            }

            def aware_utc(value):
                if not value:
                    return None
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)

            joined = aware_utc(user_record.get('joined_at'))
            joined_text = self.format_dt(
                joined, timezone_name, '%d %b %Y, %I:%M %p %Z'
            ) if joined else 'Unknown'
            referral_link = f'https://t.me/{me.username}?start=ref_{q.from_user.id}'
            username = f'@{q.from_user.username}' if q.from_user.username else 'Not set'
            full_name = ' '.join(
                value for value in [q.from_user.first_name, q.from_user.last_name] if value
            ) or 'Unknown'

            lines = [
                '👤 My Profile',
                '',
                f'🆔 User ID: {q.from_user.id}',
                f'👤 Name: {full_name}',
                f'📝 Username: {username}',
                f"🌐 Language: {q.from_user.language_code or 'Unknown'}",
                f'📅 Joined: {joined_text}',
                f'👥 Total Referrals: {total_referrals}',
                f'✅ Successful Referrals: {successful_referrals}',
                '',
                '🔗 Referral Link:',
                referral_link,
                '',
                '━━━━━━━━━━━━━━━━━━━━',
                '📋 Subscription Details',
            ]

            now = datetime.now(timezone.utc)
            currency = (seller_settings or {}).get('currency')

            # Plan Group subscriptions are independent. Never merge them into
            # one subscription block: each purchased group keeps its own plan,
            # amount, duration and expiry.
            if group_subs:
                for index, sub in enumerate(group_subs, start=1):
                    expiry = aware_utc(sub.get('expiry_date'))
                    active = bool(
                        sub.get('active') and expiry and expiry > now
                    )
                    group_id = str(sub.get('group_id') or '').strip()

                    group = plan_groups_by_id.get(group_id)

                    target_titles = []
                    for target in (group or {}).get('targets') or []:
                        title = str(target.get('title') or '').strip()
                        if title and title not in target_titles:
                            target_titles.append(title)
                    if target_titles:
                        group_label = ', '.join(target_titles)
                    else:
                        group_label = f'Plan Group {index}'

                    lines.extend([
                        '',
                        f'📦 Plan Group {index}: {group_label}',
                    ])

                    if active:
                        remaining = expiry - now
                        days = max(remaining.days, 0)
                        hours = remaining.seconds // 3600
                        minutes = remaining.seconds % 3600 // 60
                        start = aware_utc(
                            sub.get('start_date') or sub.get('created_at')
                        )
                        start_text = self.format_dt(
                            start, timezone_name, '%d %b %Y, %I:%M %p %Z'
                        ) if start else 'Unknown'
                        expiry_text = self.format_dt(
                            expiry, timezone_name, '%d %b %Y, %I:%M %p %Z'
                        )
                        amount = sub.get('amount')
                        amount_text = (
                            format_currency(currency, amount)
                            if isinstance(amount, (int, float))
                            else str(amount or '—')
                        )
                        lines.extend([
                            '📌 Status: ✅ Active',
                            f"💎 Plan: {sub.get('plan') or 'Unknown'}",
                            f'💰 Amount: {amount_text}',
                            f"⏳ Duration: {sub.get('duration_text') or '—'}",
                            f'📅 Start Date: {start_text}',
                            f'📅 Expiry: {expiry_text}',
                            f'⏱ Time Left: {days}d {hours}h {minutes}m',
                        ])
                    else:
                        expiry_text = (
                            self.format_dt(expiry, timezone_name, '%d %b %Y, %I:%M %p %Z')
                            if expiry else '—'
                        )
                        amount = sub.get('amount')
                        amount_text = (
                            format_currency(currency, amount)
                            if isinstance(amount, (int, float))
                            else str(amount or '—')
                        )
                        lines.extend([
                            '📌 Status: ❌ No Active Subscription',
                            f"💎 Last Plan: {sub.get('plan') or '—'}",
                            f'💰 Amount: {amount_text}',
                            f"⏳ Duration: {sub.get('duration_text') or '—'}",
                            f'📅 Expiry: {expiry_text}',
                        ])

                    if index < len(group_subs):
                        lines.append('━━━━━━━━━━━━━━━━━━━━')
            else:
                # Keep existing legacy subscription behaviour for users whose
                # subscription predates Plan Groups or was created by an older
                # payment path.
                sub = legacy_sub or {}
                expiry = aware_utc(sub.get('expiry_date'))
                active = bool(sub and sub.get('active') and expiry and expiry > now)
                if active:
                    remaining = expiry - now
                    days = max(remaining.days, 0)
                    hours = remaining.seconds // 3600
                    minutes = remaining.seconds % 3600 // 60
                    start = aware_utc(sub.get('start_date') or sub.get('created_at'))
                    start_text = self.format_dt(
                        start, timezone_name, '%d %b %Y, %I:%M %p %Z'
                    ) if start else 'Unknown'
                    expiry_text = self.format_dt(
                        expiry, timezone_name, '%d %b %Y, %I:%M %p %Z'
                    )
                    amount = sub.get('amount')
                    amount_text = (
                        format_currency(currency, amount)
                        if isinstance(amount, (int, float))
                        else str(amount or '—')
                    )
                    lines.extend([
                        '📌 Status: ✅ Active',
                        f"💎 Plan: {sub.get('plan') or 'Unknown'}",
                        f'💰 Amount: {amount_text}',
                        f"⏳ Duration: {sub.get('duration_text') or '—'}",
                        f'📅 Start Date: {start_text}',
                        f'📅 Expiry: {expiry_text}',
                        f'⏱ Time Left: {days}d {hours}h {minutes}m',
                    ])
                else:
                    expiry_text = (
                        self.format_dt(expiry, timezone_name, '%d %b %Y, %I:%M %p %Z')
                        if expiry else '—'
                    )
                    lines.extend([
                        '📌 Status: ❌ No Active Subscription',
                        f"💎 Last Plan: {sub.get('plan') or '—'}",
                        f"💰 Amount: {sub.get('amount') or '—'}",
                        f"⏳ Duration: {sub.get('duration_text') or '—'}",
                        f'📅 Expiry: {expiry_text}',
                    ])

            await self.safe_query_message(q, '\n'.join(lines), back_keyboard)
        except Exception as exc:
            logger.exception('Profile failed owner=%s user=%s', owner, q.from_user.id)
            await q.message.reply_text(
                f'❌ Profile could not be loaded.\nError: {str(exc)[:250]}',
                reply_markup=back_keyboard,
            )
        return True
    return False
