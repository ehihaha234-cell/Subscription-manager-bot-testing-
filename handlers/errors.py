from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from handlers.common.error_handler import notify_update_error, report_exception


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return

    source = "callback_query" if isinstance(update, Update) and update.callback_query else "telegram_update"
    await report_exception(error, update=update, source=source)
    await notify_update_error(update)
