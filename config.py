import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# Telegram
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()

# Telegram MTProto credentials used only by the optional Business Automation
# account-connection flow. Create these at my.telegram.org. They are not bot
# tokens and are intentionally optional so the main SaaS bot can still start
# when Business Automation is not configured.
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()

# ==========================================
# MongoDB
# ==========================================

MONGO_URI = os.getenv("MONGO_URI", "").strip()
DATABASE_NAME = os.getenv(
    "DATABASE_NAME",
    "telegram_subscription_bot"
).strip()

# ==========================================
# Admin
# ==========================================

ADMIN_IDS = [
    int(admin.strip())
    for admin in os.getenv("ADMIN_IDS", "").split(",")
    if admin.strip()
]

# ==========================================
# Subscription
# ==========================================

DEFAULT_PLAN_DAYS = int(
    os.getenv("DEFAULT_PLAN_DAYS", 30)
)

TIMEZONE = os.getenv(
    "TIMEZONE",
    "Asia/Kolkata"
)

# ==========================================
# UPI
# ==========================================

UPI_ID = os.getenv("UPI_ID", "").strip()
UPI_NAME = os.getenv("UPI_NAME", "").strip()
QR_IMAGE = os.getenv(
    "QR_IMAGE",
    "assets/upi_qr.png"
)

# ==========================================
# Razorpay
# ==========================================

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

# ==========================================
# Stripe
# ==========================================

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

# ==========================================
# Referral
# ==========================================

REFERRAL_BONUS_DAYS = int(
    os.getenv("REFERRAL_BONUS_DAYS", 7)
)

REFERRAL_COMMISSION_PERCENT = int(
    os.getenv("REFERRAL_COMMISSION_PERCENT", 10)
)

# ==========================================
# Coupons
# ==========================================

ENABLE_COUPONS = (
    os.getenv("ENABLE_COUPONS", "True").lower() == "true"
)

# ==========================================
# Scheduler
# ==========================================

EXPIRY_CHECK_INTERVAL_MINUTES = int(
    os.getenv("EXPIRY_CHECK_INTERVAL_MINUTES", 10)
)

REMINDER_BEFORE_EXPIRY_DAYS = int(
    os.getenv("REMINDER_BEFORE_EXPIRY_DAYS", 3)
)

# ==========================================
# Dashboard
# ==========================================

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 10000))

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "CHANGE_ME"
)


# ==========================================
# Public Web URL / Payment Gateways
# ==========================================

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# ==========================================
# Logging
# ==========================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
)

# ==========================================
# Backup
# ==========================================

BACKUP_ENABLED = (
    os.getenv("BACKUP_ENABLED", "True").lower() == "true"
)

BACKUP_INTERVAL_HOURS = int(
    os.getenv("BACKUP_INTERVAL_HOURS", 24)
)

# ==========================================
# Validation
# ==========================================

REQUIRED = {
    "BOT_TOKEN": BOT_TOKEN,
    "MONGO_URI": MONGO_URI,
}

missing = [
    key
    for key, value in REQUIRED.items()
    if not value
]

if missing:
    raise RuntimeError(
        f"Missing environment variables: {', '.join(missing)}"
    )


def validate_runtime_config(*, strict_security: bool = True) -> None:
    """Validate startup settings after logging has been initialized."""
    problems: list[str] = []

    if not BOT_TOKEN:
        problems.append("BOT_TOKEN is missing")
    elif ":" not in BOT_TOKEN:
        problems.append("BOT_TOKEN format is invalid")

    if not MONGO_URI:
        problems.append("MONGO_URI is missing")

    if strict_security:
        if SECRET_KEY in {"", "CHANGE_ME"}:
            problems.append("SECRET_KEY must be changed from its default value")

    if problems:
        raise RuntimeError("Invalid startup configuration: " + "; ".join(problems))
