"""User callback ownership map used during staged module extraction."""

USER_ROUTE_GROUPS = {
    "home": ("c_home",),
    "plans": ("c_plans", "c_buy", "c_renew", "c_select_"),
    "payments": ("c_pg_", "c_upload"),
    "profile": ("c_profile",),
    "referral": ("c_referral", "c_refunlock"),
    "support": ("c_support",),
    "seller_plan": ("seller_current_plan", "seller_upgrade_plan"),
}

__all__ = ["USER_ROUTE_GROUPS"]
