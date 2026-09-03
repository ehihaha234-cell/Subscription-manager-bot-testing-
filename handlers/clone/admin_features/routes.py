"""Admin callback ownership map used during the staged module extraction.

The callback strings remain unchanged.  This map is documentation and a test
fixture; it does not alter production routing.
"""

ADMIN_ROUTE_GROUPS = {
    "dashboard": ("a_home", "a_seller_profile", "a_seller_plan_history"),
    "plans": ("a_plans", "a_plan_"),
    "channels": ("a_channels", "a_channel_"),
    "payments": ("a_payment", "a_pay_", "a_pending"),
    "users": ("a_users", "a_user_"),
    "broadcast": ("a_broadcast", "a_schedule", "a_retry"),
    "referrals": ("a_referral", "a_refunlock"),
    "coupons": ("a_coupon",),
    "settings": ("a_settings", "a_bot_settings", "a_welcome"),
    "support": ("a_live_support", "a_help", "a_staff"),
}

__all__ = ["ADMIN_ROUTE_GROUPS"]
