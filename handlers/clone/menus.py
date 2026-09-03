"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneMenusMixin:
    def main_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Plans",callback_data="c_plans"),InlineKeyboardButton("💳 Buy",callback_data="c_buy")],
            [InlineKeyboardButton("👤 My Profile",callback_data="c_profile"),InlineKeyboardButton("🔄 Renew",callback_data="c_renew")],
            [InlineKeyboardButton("🎁 Referral",callback_data="c_referral"),InlineKeyboardButton("📞 Support",callback_data="c_support")],
        ])

    @staticmethod
    def admin_menu():
        """Compact clone-bot seller panel. Existing callbacks are preserved."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Seller Profile", callback_data="a_seller_profile")],
            [InlineKeyboardButton("📦 Manage Plans", callback_data="a_plans"), InlineKeyboardButton("💳 Payment Settings", callback_data="a_payment")],
            [InlineKeyboardButton("📨 Pending Payments", callback_data="a_pending"), InlineKeyboardButton("📜 Payment History", callback_data="a_history")],
            [InlineKeyboardButton("📢 Channels / Groups", callback_data="a_channels"), InlineKeyboardButton("⚙️ Bot Settings", callback_data="a_settings")],
            [InlineKeyboardButton("👥 User Management", callback_data="a_users"), InlineKeyboardButton("👮 Staff Management", callback_data="a_staff")],
            [InlineKeyboardButton("📣 Broadcast", callback_data="a_broadcast")],
            [InlineKeyboardButton("🛡 Group Manager", callback_data="gm_home"), InlineKeyboardButton("🔒 Content Protection", callback_data="cp_home")],
            [InlineKeyboardButton("💬 Live Support", callback_data="a_live_support"), InlineKeyboardButton("💼 Business Automation", callback_data="ba_home")],
            [InlineKeyboardButton("🛡 Subscription Guard", callback_data="sg_home")],
            [InlineKeyboardButton("📅 Scheduled Broadcast", callback_data="a_broadcast_schedule")],
            [InlineKeyboardButton("📜 Terms & Policy", callback_data="a_terms")],
            [InlineKeyboardButton("🆘 Help & Commands", callback_data="a_help")],
        ])

    async def admin_panel_text(self, owner_id:int, seller_user=None):
        """Build the final live summary shown above the clone-bot admin buttons."""
        try:
            # owner_id is the clone-specific data scope. Seller subscriptions
            # belong to the real seller account, otherwise Clone 2+ can appear
            # as Free even when the seller has an active paid plan.
            # owner_id is clone data scope. Always resolve the real seller from
            # the clone record; seller_user may be a staff/admin user.
            bot_record = await get_bot_by_data_owner_id(owner_id) or {}
            seller_account_id = int(
                bot_record.get("seller_account_id")
                or bot_record.get("owner_id")
                or owner_id
            )
            plan, _assignment = await effective_plan(seller_account_id)
            settings = await get_seller_settings(owner_id)
            usage = await stats(owner_id)

            # Always display the real seller who owns this clone. The user opening
            # the panel can be a staff/admin, so seller_user must not be used as
            # the seller identity here.
            seller_record = await get_database()["sellers"].find_one({"owner_id": seller_account_id}) or {}
            seller_username = seller_record.get("username")
            seller_name = (
                seller_record.get("full_name")
                or seller_record.get("first_name")
                or seller_record.get("name")
                or None
            )
            if seller_username:
                seller_label = f"@{seller_username}"
            elif seller_name:
                seller_label = str(seller_name)
            else:
                seller_label = str(seller_account_id)
            clone_username = (bot_record.get("bot_username") or "Not configured").lstrip("@")
            clone_label = f"@{clone_username}" if clone_username != "Not configured" else clone_username
            currency = settings.get("currency") or "INR"
            symbol = currency_symbol(currency)
            runtime_status = str(bot_record.get("runtime_status") or "").lower()
            online = self.is_running(int(bot_record.get("bot_id") or 0)) or runtime_status in {"running", "online", "started"}
            status_text = "🟢 Online" if online else "🔴 Offline"

            return (
                "🛠 <b>ADMIN PANEL</b>\n\n"
                f"👤 Seller: <b>{html.escape(seller_label)}</b>\n"
                f"🤖 Clone Bot: <b>{html.escape(clone_label)}</b>\n"
                f"💎 Plan: <b>{html.escape(str(plan.get('name', 'Free')))}</b>\n"
                f"{status_text}\n\n"
                "━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: <b>{int(usage.get('total_users', usage.get('users', 0))):,}</b>\n"
                f"🟢 Active Today: <b>{int(usage.get('active_users_today', 0)):,}</b>\n"
                f"✅ Active Subscribers: <b>{int(usage.get('active_subscribers', usage.get('active', 0))):,}</b>\n"
                f"📦 Plans: <b>{int(usage.get('plans', 0)):,}</b>\n"
                f"📢 Channels / Groups: <b>{int(usage.get('channels', 0)):,}</b>\n"
                f"⏳ Pending Payments: <b>{int(usage.get('pending', 0)):,}</b>\n"
                f"💰 Today Revenue: <b>{symbol}{float(usage.get('today_revenue', 0) or 0):,.2f}</b>\n"
                f"💵 Total Revenue: <b>{symbol}{float(usage.get('total_revenue', usage.get('revenue', 0)) or 0):,.2f}</b>"
            )
        except Exception:
            logger.exception("Failed to build seller admin summary owner=%s", owner_id)
            return "🛠 <b>ADMIN PANEL</b>\n\n⚠️ Live summary is temporarily unavailable."

    @staticmethod
    def back(target="a_home"): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back",callback_data=target)]])

    @staticmethod
    def limit_keyboard(back_target="a_home"):
        """Buttons shown when a seller reaches a clone-bot plan limit.

        Seller-plan purchases are handled by the main SaaS bot. A URL button is
        used here because callbacks from a clone bot cannot be processed by the
        main bot. The current-plan button stays inside the clone bot and opens
        the existing seller profile/current-plan page.
        """
        main_bot_username = os.getenv(
            "MAIN_BOT_USERNAME", "Subscripti0n_Manage_bot"
        ).strip().lstrip("@")
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Buy / Change Plan",
                url=f"https://t.me/{main_bot_username}?start=sellerplan",
            )],
            [InlineKeyboardButton(
                "📊 View Current Plan",
                callback_data="seller_current_plan",
            )],
            [InlineKeyboardButton("❌ Close", callback_data=back_target)],
        ])

    @staticmethod
    def plans_admin_menu():
        return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Plan",callback_data="a_plan_add")],[InlineKeyboardButton("📋 View Plans",callback_data="a_plan_list")],[InlineKeyboardButton("⬅ Back",callback_data="a_home")]])

    @staticmethod
    def channels_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Channel/Group",callback_data="a_channel_add")],
            [InlineKeyboardButton("📋 Channel List",callback_data="a_channel_list")],
            [InlineKeyboardButton(
                "🔗 Resend Invite Links to Active Subscribers",
                callback_data="a_channel_resend",
            )],
            [InlineKeyboardButton("⬅ Back",callback_data="a_home")],
        ])

    @staticmethod
    def payment_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Automatic Payment Gateway",callback_data="a_pg_home")],
            [InlineKeyboardButton("⭐ Telegram Stars",callback_data="a_stars_toggle")],
            [InlineKeyboardButton("💵 Manual Payment",callback_data="a_manual_payment")],
            [InlineKeyboardButton("⬅ Back",callback_data="a_home")],
        ])

    @staticmethod
    def manual_payment_menu(manual_enabled: bool):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{'✅' if manual_enabled else '❌'} {'Disable' if manual_enabled else 'Enable'} Manual Payment",
                callback_data="a_manual_toggle",
            )],
            [InlineKeyboardButton("🏦 Set UPI ID",callback_data="a_set_upi_id")],
            [InlineKeyboardButton("👤 Set UPI Name",callback_data="a_set_upi_name")],
            [InlineKeyboardButton("🖼 Upload / Change QR",callback_data="a_set_qr")],
            [InlineKeyboardButton("🗑 Remove QR",callback_data="a_remove_qr")],
            [InlineKeyboardButton("👀 Preview Payment Details",callback_data="a_payment_preview")],
            [InlineKeyboardButton("⬅ Back",callback_data="a_payment")],
        ])

    @staticmethod
    def settings_menu():
        return InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Bot Name",callback_data="a_set_bot_name")],[InlineKeyboardButton("💬 Welcome Message",callback_data="a_welcome")],[InlineKeyboardButton("📞 Support Username",callback_data="a_set_support")],[InlineKeyboardButton("💵 Currency",callback_data="a_set_currency"),InlineKeyboardButton("🕒 Timezone",callback_data="a_set_timezone")],[InlineKeyboardButton("🔔 Reminder Days",callback_data="a_set_reminder")],[InlineKeyboardButton("🎁 Referral Reward Days",callback_data="a_set_referral_days"),InlineKeyboardButton("🔓 Referral Unlock",callback_data="a_referral_unlock")],[InlineKeyboardButton("⬅ Back",callback_data="a_home")]])

    @staticmethod
    def staff_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Promote Admin", callback_data="a_staff_add_admin"), InlineKeyboardButton("➕ Promote Moderator", callback_data="a_staff_add_moderator")],
            [InlineKeyboardButton("📋 Staff List", callback_data="a_staff_list")],
            [InlineKeyboardButton("⬅ Back", callback_data="a_home")],
        ])

    @staticmethod
    def staff_item_menu(user_id:int, suspended:bool=False):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Activate" if suspended else "⏸ Suspend", callback_data=f"a_staff_status_{user_id}_{'active' if suspended else 'suspended'}")],
            [InlineKeyboardButton("❌ Remove Staff", callback_data=f"a_staff_remove_{user_id}")],
            [InlineKeyboardButton("⬅ Staff List", callback_data="a_staff_list")],
        ])

