"""Shared message-editor helpers for clone-bot editable messages."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import quote

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup


FEATURE_CALLBACKS: dict[str, str] = {
    "plans": "c_plans",
    "buy": "c_buy",
    "profile": "c_profile",
    "renew": "c_renew",
    "referral": "c_referral",
    "referral_unlock": "c_referral_unlock",
    "support": "c_support",
    "home": "c_home",
}


def url_buttons_header() -> str:
    """Exact instruction header used by the welcome button editor."""
    return (
        "👉 Set the buttons to be placed under the message\n\n"
        "Send a message structured as follows:\n\n"
        "• Add a single button:\n"
        "Button title - t.me/LinkExample\n\n"
        "• Add multiple buttons on a single line:\n"
        "Button title - t.me/LinkExample && Button text - t.me/LinkExample\n\n"
        "• Add multiple rows of buttons:\n"
        "Button title - t.me/LinkExample\n"
        "Button title - t.me/LinkExample\n\n"
        "⭐ Special Buttons\n\n"
        "• Add a button that shows a popup:\n"
        "Button title - popup: Popup text\n"
        "or\n"
        "Button title - alert: Popup text\n\n"
        "• Add a button with a link to the group rules:\n"
        "Button title - rules\n\n"
        "• Add a share button:\n"
        "Button title - share: Text to be shared\n\n"
        "• Add a button with copyable text:\n"
        "Button title - copy: Text copied on click\n\n"
        "⚡ Feature Buttons\n\n"
        "• Add a feature button:\n"
        "Button title - feature: feature_name\n\n"
        "Available feature names:\n"
        "plans, buy, profile, renew, referral, referral_unlock, support, home"
    )




# Backward-compatible name used by Business Automation.
business_url_buttons_header = url_buttons_header

def _parse_target(target: str, line_no: int, button_no: int) -> dict[str, str]:
    location = f"Line {line_no}, button {button_no}: "
    if target.startswith(("http://", "https://", "tg://")) or target.startswith("t.me/"):
        if target.startswith("t.me/"):
            target = "https://" + target
        return {"text_type": "url", "value": target}

    if target.startswith("@"):
        username = target[1:].strip()
        if not username or len(username) > 32 or not all(ch.isalnum() or ch == "_" for ch in username):
            raise ValueError(location + "invalid Telegram username. Use: Button title - @username")
        return {"text_type": "url", "value": f"https://t.me/{username}"}

    if target.startswith("feature:"):
        feature = target.split(":", 1)[1].strip().lower()
        callback = FEATURE_CALLBACKS.get(feature)
        if not callback:
            supported = ", ".join(FEATURE_CALLBACKS)
            raise ValueError(location + f"unknown feature '{feature}'. Available: {supported}")
        return {"text_type": "callback", "value": callback}

    for prefix, action in (("popup:", "popup"), ("alert:", "alert"), ("share:", "share"), ("copy:", "copy")):
        if target.startswith(prefix):
            value = target[len(prefix):].strip()
            if not value:
                raise ValueError(location + f"{prefix} requires text. Example: Button title - {prefix}Popup text")
            return {"text_type": action, "value": value}

    if target == "rules":
        return {"text_type": "rules", "value": ""}

    raise ValueError(
        location + "invalid format. Use URL, @username, popup:, alert:, rules, share:, copy:, or feature:<name>."
    )


def parse_editor_buttons(text: str) -> list[list[dict[str, str]]]:
    """Parse button rows and return precise line/button format errors."""
    rows: list[list[dict[str, str]]] = []
    for line_no, raw_line in enumerate((text or "").splitlines(), 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        row: list[dict[str, str]] = []
        for button_no, item in enumerate(raw_line.split("&&"), 1):
            item = item.strip()
            if " - " not in item:
                raise ValueError(f"Line {line_no}, button {button_no}: missing ' - '. Example: Button title - t.me/LinkExample")
            title, target = [part.strip() for part in item.split(" - ", 1)]
            if not title:
                raise ValueError(f"Line {line_no}, button {button_no}: button title is empty.")
            if not target:
                raise ValueError(f"Line {line_no}, button {button_no}: button target is empty.")
            parsed = _parse_target(target, line_no, button_no)
            row.append({"text": title, "type": parsed["text_type"], "value": parsed["value"]})
        rows.append(row)

    if not rows:
        raise ValueError("No buttons found. Add at least one line using: Button title - t.me/LinkExample")
    return rows


def build_editor_keyboard(
    rows: Iterable[Iterable[dict[str, Any]]] | None,
    clone_username: str = "",
) -> InlineKeyboardMarkup | None:
    """Build URL, feature, special and clone buttons from the stored editor schema."""
    if not rows:
        return None
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        built: list[InlineKeyboardButton] = []
        for item in row:
            text = str(item.get("text") or "Button")
            kind = item.get("type")
            value = str(item.get("value") or "")
            if kind == "url":
                if value:
                    built.append(InlineKeyboardButton(text, url=value))
            elif kind == "callback":
                built.append(InlineKeyboardButton(text, callback_data=value or "c_home"))
            elif kind == "copy":
                built.append(InlineKeyboardButton(text, copy_text=CopyTextButton(value[:256])))
            elif kind == "share":
                built.append(InlineKeyboardButton(text, url=f"https://t.me/share/url?text={quote(value)}"))
            elif kind == "clone":
                username = str(clone_username or "").lstrip("@")
                if username:
                    start_param = value or "home"
                    built.append(InlineKeyboardButton(text, url=f"https://t.me/{username}?start={quote(start_param)}"))
            elif kind in {"popup", "alert", "rules"}:
                # Keep the payload compact enough for Telegram's 64-byte callback limit.
                if kind == "rules":
                    callback = "w_rules"
                else:
                    encoded = quote(value, safe="")
                    callback = f"w_{kind}:{encoded}"
                    if len(callback.encode("utf-8")) > 64:
                        # Long popup text cannot be carried by callback_data; keep the button valid.
                        callback = "w_popup_long"
                built.append(InlineKeyboardButton(text, callback_data=callback))
        if built:
            keyboard.append(built)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def editor_header(title: str, item: dict[str, Any], *, variables: str = "") -> str:
    button_count = sum(len(row) for row in (item.get("buttons") or []))
    media_count = len(item.get("media") or ([] if not item.get("media_file_id") else [{"file_id": item.get("media_file_id")}]))
    lines = [
        title,
        "",
        f"Status: {'🟢 Enabled' if item.get('enabled', True) else '🔴 Disabled'}",
        f"📝 Text: {'✅ Added' if item.get('text') else '❌ Not added'}",
        f"🖼 Media: {media_count}/10" if media_count else "🖼 Media: ❌ Not added",
        f"🔗 Buttons: {button_count}",
    ]
    if variables:
        lines.extend(["", f"Variables: {variables}"])
    lines.extend(["", "Use the options below to add, replace, preview, or remove each part."])
    return "\n".join(lines)


def editor_menu_keyboard(
    prefix: str,
    item: dict[str, Any],
    *,
    back_callback: str,
    allow_toggle: bool = True,
    delete_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if allow_toggle:
        rows.append([InlineKeyboardButton("🔴 Disable" if item.get("enabled", True) else "🟢 Enable", callback_data=f"{prefix}_toggle")])
    rows.extend([
        [InlineKeyboardButton("📝 Text", callback_data=f"{prefix}_text"), InlineKeyboardButton("🖼 Media", callback_data=f"{prefix}_media")],
        [InlineKeyboardButton("🔗 Buttons", callback_data=f"{prefix}_buttons")],
        [InlineKeyboardButton("👁 Full Preview", callback_data=f"{prefix}_preview")],
    ])
    if delete_callback:
        rows.append([InlineKeyboardButton("🗑 Delete", callback_data=delete_callback)])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(rows)


def editor_text_prompt(title: str, *, variables: str = "") -> str:
    text = f"📝 {title}\n\nSend the message text. HTML formatting is supported."
    if variables:
        text += f"\n\nAvailable variables:\n{variables}"
    return text


def editor_media_prompt(title: str) -> str:
    return (
        f"🖼 {title}\n\n"
        "Send one photo/video/document, or select and send one Telegram album together. "
        "The complete message or album will replace the current media automatically (maximum 10 files).\n\n"
        "Send the complete album in one selection; it will save automatically."
    )
