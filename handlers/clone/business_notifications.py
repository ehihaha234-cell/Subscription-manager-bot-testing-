"""Mirror subscriber lifecycle notifications into connected Official Business chats."""
import logging
from database.business_delivery import list_business_contact_routes

logger = logging.getLogger(__name__)


async def send_business_notification(
    bot, owner_id: int, user_id: int, text: str, *,
    reply_markup=None, parse_mode=None, disable_web_page_preview=True,
) -> dict:
    """Send a copy through every active Official Business connection for this user.

    The regular clone-bot DM remains the primary delivery. This helper only mirrors
    it to Business chats and never changes subscription/payment state.
    """
    try:
        routes = await list_business_contact_routes(int(owner_id), int(user_id))
    except Exception as exc:
        logger.exception("Business notification route lookup failed owner=%s user=%s", owner_id, user_id)
        return {"sent": 0, "failed": 1, "routes": 0, "error": str(exc)}

    sent = failed = 0
    seen = set()
    for route in routes:
        if str(route.get("mode") or "").lower() != "official":
            continue
        connection_id = str(route.get("connection_id") or "").strip()
        chat_id = int(route.get("chat_id") or user_id)
        key = (connection_id, chat_id)
        if not connection_id or key in seen:
            continue
        seen.add(key)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                business_connection_id=connection_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.exception(
                "Business lifecycle notification failed owner=%s user=%s connection=%s chat=%s",
                owner_id, user_id, connection_id, chat_id,
            )
    return {"sent": sent, "failed": failed, "routes": len(seen), "error": ""}
