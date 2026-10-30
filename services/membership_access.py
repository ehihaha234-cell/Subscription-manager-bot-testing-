"""Reliable Telegram member removal and ban recovery helpers."""
import asyncio


async def unban_and_verify(bot, chat_id: int, user_id: int, attempts: int = 3):
    """Clear a ban and verify Telegram no longer reports the user as kicked."""
    last_error = None
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            await bot.unban_chat_member(
                chat_id=int(chat_id), user_id=int(user_id), only_if_banned=True
            )
        except Exception as exc:
            last_error = exc
        try:
            member = await bot.get_chat_member(
                chat_id=int(chat_id), user_id=int(user_id)
            )
            if getattr(member, "status", "") != "kicked":
                return member
            last_error = RuntimeError("Telegram still reports the user as kicked after unban")
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            await asyncio.sleep(0.35)
    raise RuntimeError(f"Could not clear Telegram ban after {attempts} attempts: {last_error}")


async def kick_then_unban(bot, chat_id: int, user_id: int) -> str:
    """Kick once, immediately unban, and verify the user is no longer banned."""
    member = await bot.get_chat_member(chat_id=int(chat_id), user_id=int(user_id))
    status = getattr(member, "status", "")
    if status == "kicked":
        await unban_and_verify(bot, chat_id, user_id)
        return "unbanned_existing"
    if status in {"left", "creator", "administrator"}:
        return "already_left"
    await bot.ban_chat_member(chat_id=int(chat_id), user_id=int(user_id))
    await unban_and_verify(bot, chat_id, user_id)
    return "removed"
