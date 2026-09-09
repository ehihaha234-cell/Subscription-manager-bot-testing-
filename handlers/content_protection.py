from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from database.staff import active_staff

from database.content_protection import (
    get_content_protection_settings,
    set_content_protection_enabled,
)


def protection_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔴 Turn Protection OFF" if enabled else "🟢 Turn Protection ON",
            callback_data="cp_toggle",
        )],
        [InlineKeyboardButton("⬅ Admin Panel", callback_data="a_home")],
    ])


def protection_text(enabled: bool) -> str:
    return (
        "🔒 Content Protection\n\n"
        f"Status: {'🟢 ON' if enabled else '🔴 OFF'}\n\n"
        "When ON, all new messages and media sent by this clone bot to normal users are protected.\n\n"
        "Protected content includes:\n"
        "• Welcome messages\n"
        "• Broadcast and scheduled broadcast\n"
        "• Live-support replies\n"
        "• Payment and subscription messages\n"
        "• Photos, videos, documents, audio and other media\n\n"
        "Users normally cannot forward, copy or save protected content. "
        "Old messages are not changed. Seller/admin messages and connected groups/channels are not affected."
    )


async def content_protection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = int(context.application.bot_data.get("seller_owner_id") or 0)
    seller_id = int(context.application.bot_data.get("seller_account_id") or 0)

    if not query or not owner_id or not seller_id:
        return

    # Seller and promoted Admin have full seller-level management access.
    # Moderator is intentionally excluded from Content Protection.
    staff = await active_staff(owner_id, int(query.from_user.id))
    is_admin = bool(staff and str(staff.get("role") or "").lower() == "admin")
    if query.from_user.id != seller_id and not is_admin:
        await query.answer("Not authorized.", show_alert=True)
        return

    await query.answer()
    action = query.data
    settings = await get_content_protection_settings(owner_id)
    enabled = bool(settings.get("enabled", False))

    if action == "cp_toggle":
        enabled = not enabled
        await set_content_protection_enabled(owner_id, enabled)
        setter = getattr(context.bot, "set_content_protection", None)
        if callable(setter):
            setter(enabled)

    await query.edit_message_text(
        protection_text(enabled),
        reply_markup=protection_keyboard(enabled),
    )


def content_protection_handlers():
    return [CallbackQueryHandler(content_protection_callback, pattern=r"^cp_(home|toggle)$")]
