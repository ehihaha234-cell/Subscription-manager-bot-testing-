"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import feature_back_callback


async def handle(self, update, context, q, owner, action):
    back_keyboard = self.back(feature_back_callback(context))
    if action == 'c_profile':
        try:
            timezone_name = await self.seller_timezone(owner)
            user_record = await get_user(owner, q.from_user.id) or {}
            sub = await get_subscription(owner, q.from_user.id)
            me = await context.bot.get_me()

            def aware_utc(value):
                if not value:
                    return None
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
            joined = aware_utc(user_record.get('joined_at'))
            joined_text = self.format_dt(joined, timezone_name, '%d %b %Y, %I:%M %p %Z') if joined else 'Unknown'
            referral_link = f'https://t.me/{me.username}?start=ref_{q.from_user.id}'
            total_referrals = await count_all_referrals(owner, q.from_user.id)
            successful_referrals = await count_successful_referrals(owner, q.from_user.id)
            username = f'@{q.from_user.username}' if q.from_user.username else 'Not set'
            full_name = ' '.join((value for value in [q.from_user.first_name, q.from_user.last_name] if value)) or 'Unknown'
            lines = ['👤 My Profile', '', f'🆔 User ID: {q.from_user.id}', f'👤 Name: {full_name}', f'📝 Username: {username}', f"🌐 Language: {q.from_user.language_code or 'Unknown'}", f'📅 Joined: {joined_text}', f'👥 Total Referrals: {total_referrals}', f'✅ Successful Referrals: {successful_referrals}', '', '🔗 Referral Link:', referral_link, '', '━━━━━━━━━━━━━━━━━━━━', '📋 Subscription Details']
            now = datetime.now(timezone.utc)
            expiry = aware_utc((sub or {}).get('expiry_date'))
            active = bool(sub and sub.get('active') and expiry and (expiry > now))
            if active:
                remaining = expiry - now
                days = max(remaining.days, 0)
                hours = remaining.seconds // 3600
                minutes = remaining.seconds % 3600 // 60
                start = aware_utc(sub.get('start_date') or sub.get('created_at'))
                start_text = self.format_dt(start, timezone_name, '%d %b %Y, %I:%M %p %Z') if start else 'Unknown'
                expiry_text = self.format_dt(expiry, timezone_name, '%d %b %Y, %I:%M %p %Z')
                amount = sub.get('amount')
                currency = (await get_seller_settings(owner)).get('currency')
                amount_text = format_currency(currency, amount) if isinstance(amount, (int, float)) else str(amount or '—')
                lines.extend(['📌 Status: ✅ Active', f"💎 Plan: {sub.get('plan') or 'Unknown'}", f'💰 Amount: {amount_text}', f"⏳ Duration: {sub.get('duration_text') or '—'}", f'📅 Start Date: {start_text}', f'📅 Expiry: {expiry_text}', f'⏱ Time Left: {days}d {hours}h {minutes}m'])
            else:
                lines.extend(['📌 Status: ❌ No Active Subscription', f"💎 Last Plan: {(sub or {}).get('plan') or '—'}", f"💰 Amount: {(sub or {}).get('amount') or '—'}", f"⏳ Duration: {(sub or {}).get('duration_text') or '—'}", f'📅 Expiry: {self.format_dt(expiry)}'])
            await self.safe_query_message(q, '\n'.join(lines), back_keyboard)
        except Exception as exc:
            logger.exception('Profile failed owner=%s user=%s', owner, q.from_user.id)
            await q.message.reply_text(f'❌ Profile could not be loaded.\nError: {str(exc)[:250]}', reply_markup=back_keyboard)
        return True
    return False
