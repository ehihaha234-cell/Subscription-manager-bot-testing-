"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_home':
        context.user_data.clear()
        await q.edit_message_text(await self.admin_panel_text(owner, q.from_user), reply_markup=self.admin_menu(), parse_mode='HTML')
        return True
    if a == 'a_seller_profile':
        timezone_name = await self.seller_timezone(owner)
        seller_account_id = self.seller_account(context)
        plan, assignment = await effective_plan(seller_account_id)
        usage = await stats(owner)
        bot_record = await get_bot_by_data_owner_id(owner) or {}
        expiry = (assignment or {}).get('expiry_date')
        if expiry and getattr(expiry, 'tzinfo', None) is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if expiry and expiry > now:
            remaining = expiry - now
            remaining_text = f'{remaining.days}d {remaining.seconds // 3600}h {remaining.seconds % 3600 // 60}m'
            status = '✅ Active'
        elif str(plan.get('plan_id', 'free')) == 'free':
            remaining_text = 'No expiry'
            status = '🆓 Free Plan'
        else:
            remaining_text = 'Expired'
            status = '❌ Expired'

        def lim(value):
            try:
                value = int(value)
                return 'Unlimited' if value < 0 else f'{value:,}'
            except Exception:
                return str(value)
        username_text = f'@{q.from_user.username}' if q.from_user.username else 'Not set'
        text = f"👤 Seller Profile\n\n🆔 Seller ID: {seller_account_id}\n👤 Name: {q.from_user.full_name or 'Unknown'}\n📝 Username: {username_text}"
        referral_data = await seller_referral_stats(seller_account_id)
        main_bot_username = os.getenv('MAIN_BOT_USERNAME', 'Subscripti0n_Manage_bot').lstrip('@')
        referral_link = f'https://t.me/{main_bot_username}?start=refseller_{seller_account_id}'

        text += f"\n\n💎 Plan Details\nPlan: {plan.get('name', 'Free')}\nStatus: {status}\nExpiry: {self.format_dt(expiry, timezone_name)}\nRemaining: {remaining_text}\n\n📊 Usage & Limits\n🤖 Clone Bots: {(1 if bot_record else 0)} / {lim(plan.get('bot_limit', 1))}\n👥 Active Subscribers: {usage.get('active', 0)} / {lim(plan.get('active_subscriber_limit', 25))}\n📢 Channels / Groups: {usage.get('channels', 0)} / {lim(plan.get('channel_limit', 1))}\n📦 Subscription Plans: {usage.get('plans', 0)} / {lim(plan.get('plan_limit', 2))}\n\n👥 Total Users: {usage.get('users', 0)}\n💳 Pending Payments: {usage.get('pending', 0)}\n💰 Revenue: {format_currency((await get_seller_settings(owner)).get('currency'), usage.get('revenue', 0))}"
        text += (
            f"\n\n🤝 Seller Referral Program"
            f"\n\n👥 Sellers Joined: {referral_data.get('total', 0)}"
            f"\n🎁 Rewards Received: {referral_data.get('rewarded', 0)}"
            f"\n\nShare this link with new sellers:"
            f"\n{referral_link}"
            f"\n\nThe owner controls reward days and reward plan from "
            f"Owner Dashboard → Subscription Management."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💎 Buy / Change Plan', url=f'https://t.me/{main_bot_username}?start=sellerplan')],
            [InlineKeyboardButton('📤 Share Referral Link', url=f'https://t.me/share/url?url={referral_link}')],
            [InlineKeyboardButton('⬅ Seller Admin Panel', callback_data='a_home')],
        ])
        await q.edit_message_text(text, reply_markup=kb, disable_web_page_preview=True)
        return True
    if a == 'a_seller_plan_history':
        await q.edit_message_text('📜 Seller Plan History\n\nOpen the main SaaS bot → Seller Dashboard → Plan History to view complete seller plan records.', reply_markup=self.back('a_seller_profile'))
        return True
    return False
