"""Return feature-button navigation to the message that opened it."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from telegram.error import TelegramError

_MAX = 5000
_ORIGINS: OrderedDict[tuple[int, int], dict[str, Any]] = OrderedDict()
_USER_ORIGINS: OrderedDict[int, dict[str, Any]] = OrderedDict()


def _message_key(message) -> tuple[int, int] | None:
    """Return a stable key for normal and Telegram Business messages."""
    if message is None:
        return None
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
    if message_id is None or chat_id is None:
        return None
    try:
        return int(chat_id), int(message_id)
    except (TypeError, ValueError):
        return None


def _user_key(query) -> int | None:
    user = getattr(query, "from_user", None)
    user_id = getattr(user, "id", None)
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


def _remember_user_origin(query, payload: dict[str, Any]) -> None:
    user_id = _user_key(query)
    if user_id is None:
        return
    _USER_ORIGINS[user_id] = dict(payload)
    _USER_ORIGINS.move_to_end(user_id)
    while len(_USER_ORIGINS) > _MAX:
        _USER_ORIGINS.popitem(last=False)


def register_feature_origin(message, *, text: str = "", markup=None) -> None:
    key = _message_key(message)
    if key is None:
        return
    _ORIGINS[key] = {"text": str(text or ""), "markup": markup}
    _ORIGINS.move_to_end(key)
    while len(_ORIGINS) > _MAX:
        _ORIGINS.popitem(last=False)


def capture_feature_origin(query, context) -> bool:
    message = getattr(query, "message", None)
    if message is None:
        return False
    key = _message_key(message)
    if key is None:
        return False
    origin = _ORIGINS.get(key)
    if not origin:
        # Business Automation messages can be created by a different bot
        # application instance, so the in-memory registry may not contain the
        # message. Capture the currently displayed message directly instead.
        text = str(getattr(message, "caption", None) or getattr(message, "text", None) or "")
        markup = getattr(message, "reply_markup", None)
        if not text and markup is None:
            return False
        origin = {"text": text, "markup": markup}

    # Persist the origin by message ID as well as in PTB context. Telegram
    # Business callbacks can be handled by a different Application instance,
    # where user_data may not be the same dictionary. The process-wide message
    # registry keeps Back navigation tied to the exact broadcast message.
    _ORIGINS[key] = {"text": str(origin.get("text") or ""), "markup": origin.get("markup")}
    _ORIGINS.move_to_end(key)
    while len(_ORIGINS) > _MAX:
        _ORIGINS.popitem(last=False)

    payload = {**_ORIGINS[key], "chat_id": key[0], "message_id": key[1]}
    # Telegram Business callbacks and the normal clone-bot callback can be
    # processed by different PTB Application instances. Keep a process-wide
    # per-user copy so the Back button can still restore the exact message
    # after that message has been edited into Plans/Profile/etc.
    _remember_user_origin(query, payload)
    try:
        context.user_data["clone_feature_origin"] = payload
        try:
            context.chat_data["clone_feature_origin"] = payload
        except Exception:
            pass
        return True
    except Exception:
        # Origin tracking must never stop the actual feature button action.
        return False


def feature_back_callback(context) -> str:
    try:
        if context.user_data.get("clone_feature_origin"):
            return "c_return_origin"
    except Exception:
        pass
    try:
        if context.chat_data.get("clone_feature_origin"):
            return "c_return_origin"
    except Exception:
        pass
    return "c_home"


async def restore_feature_origin(query, context) -> bool:
    origin = None
    source = ""
    try:
        origin = context.user_data.get("clone_feature_origin")
        if origin:
            source = "user_data"
    except Exception:
        pass
    if not origin:
        try:
            origin = context.chat_data.get("clone_feature_origin")
            if origin:
                source = "chat_data"
        except Exception:
            pass
    user_id = _user_key(query)
    if not origin and user_id is not None:
        origin = _USER_ORIGINS.get(user_id)
        if origin:
            source = "user_registry"
    if not origin:
        key = _message_key(getattr(query, "message", None))
        if key is not None:
            origin = _ORIGINS.get(key)
            if origin:
                source = "message_registry"
    if not origin:
        return False

    text = str(origin.get("text") or "")
    markup = origin.get("markup")
    try:
        message = getattr(query, "message", None)
        has_media = bool(
            getattr(message, "caption", None) is not None
            or getattr(message, "photo", None)
            or getattr(message, "video", None)
            or getattr(message, "document", None)
            or getattr(message, "animation", None)
            or getattr(message, "audio", None)
        )
        if has_media:
            await query.edit_message_caption(caption=text or None, reply_markup=markup)
        else:
            await query.edit_message_text(text=text or "Choose an option below.", reply_markup=markup)
    except TelegramError as exc:
        if "message is not modified" not in str(exc).casefold():
            return False

    # Remove the transient context only after restoration succeeds. Keep the
    # process-wide user origin so repeated feature -> Back navigation from the
    # same Business Automation message remains reliable.
    try:
        context.user_data.pop("clone_feature_origin", None)
    except Exception:
        pass
    try:
        context.chat_data.pop("clone_feature_origin", None)
    except Exception:
        pass
    register_feature_origin(query.message, text=text, markup=markup)
    if user_id is not None:
        _remember_user_origin(query, {**origin, "text": text, "markup": markup})
    return True

