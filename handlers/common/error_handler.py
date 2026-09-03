from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

from telegram import Update

from database.logs import create_log
from services.logger import mask_sensitive

logger = logging.getLogger(__name__)

USER_ERROR_MESSAGE = (
    "⚠️ A temporary problem occurred. Please try again in a few seconds."
)


def _update_context(update: object) -> dict[str, Any]:
    if not isinstance(update, Update):
        return {}

    return {
        "update_id": update.update_id,
        "user_id": getattr(update.effective_user, "id", None),
        "chat_id": getattr(update.effective_chat, "id", None),
        "callback": getattr(update.callback_query, "data", None),
    }


async def report_exception(
    error: BaseException,
    *,
    update: object | None = None,
    source: str = "telegram_update",
) -> None:
    """Log and persist an exception without blocking update processing."""
    context = _update_context(update)
    safe_context = mask_sensitive(context)
    error_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )

    logger.error(
        "Unhandled error source=%s context=%s\n%s",
        source,
        safe_context,
        mask_sensitive(error_text),
    )

    message = mask_sensitive(f"source={source} context={context} error={error}")[:1800]
    try:
        await asyncio.wait_for(
            create_log(log_type="error", message=message),
            timeout=3,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Could not persist error log to MongoDB.", exc_info=True)


async def notify_update_error(update: object) -> None:
    """Send one safe generic response when the update supports replies."""
    if not isinstance(update, Update) or not update.effective_message:
        return

    try:
        await asyncio.wait_for(
            update.effective_message.reply_text(USER_ERROR_MESSAGE),
            timeout=8,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Could not send user-facing error message.", exc_info=True)
