"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from typing import Any, Iterable
from urllib.parse import quote

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from utils.branding import append_branding


# ---------------------------------------------------------------------------
# Welcome Message editor-local button engine.
# This intentionally does NOT import handlers.common.editor_engine so the
# Welcome Message editor can evolve independently from every other editor.
# The stored schema and feature callback names remain backward compatible.
# ---------------------------------------------------------------------------
WELCOME_FEATURE_CALLBACKS: dict[str, str] = {
    "plans": "c_plans",
    "buy": "c_buy",
    "profile": "c_profile",
    "renew": "c_renew",
    "referral": "c_referral",
    "referral_unlock": "c_referral_unlock",
    "support": "c_support",
    "home": "c_home",
}


def welcome_url_buttons_header() -> str:
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


def _parse_welcome_button_target(target: str, line_no: int, button_no: int) -> dict[str, str]:
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
        callback = WELCOME_FEATURE_CALLBACKS.get(feature)
        if not callback:
            raise ValueError(location + f"unknown feature '{feature}'. Available: {', '.join(WELCOME_FEATURE_CALLBACKS)}")
        return {"text_type": "callback", "value": callback}
    for prefix, action in (("popup:", "popup"), ("alert:", "alert"), ("share:", "share"), ("copy:", "copy")):
        if target.startswith(prefix):
            value = target[len(prefix):].strip()
            if not value:
                raise ValueError(location + f"{prefix} requires text.")
            return {"text_type": action, "value": value}
    if target == "rules":
        return {"text_type": "rules", "value": ""}
    raise ValueError(location + "invalid format. Use URL, @username, popup:, alert:, rules, share:, copy:, or feature:<name>.")


def parse_welcome_buttons(text: str) -> list[list[dict[str, str]]]:
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
            parsed = _parse_welcome_button_target(target, line_no, button_no)
            row.append({"text": title, "type": parsed["text_type"], "value": parsed["value"]})
        rows.append(row)
    if not rows:
        raise ValueError("No buttons found. Add at least one line using: Button title - t.me/LinkExample")
    return rows


def build_welcome_keyboard(rows: Iterable[Iterable[dict[str, Any]]] | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        built: list[InlineKeyboardButton] = []
        for item in row:
            text = str(item.get("text") or "Button")
            kind = item.get("type")
            value = str(item.get("value") or "")
            if kind == "url" and value:
                built.append(InlineKeyboardButton(text, url=value))
            elif kind == "callback":
                built.append(InlineKeyboardButton(text, callback_data=value or "c_home"))
            elif kind == "copy":
                built.append(InlineKeyboardButton(text, copy_text=CopyTextButton(value[:256])))
            elif kind == "share":
                built.append(InlineKeyboardButton(text, url=f"https://t.me/share/url?text={quote(value)}"))
            elif kind in {"popup", "alert", "rules"}:
                callback = "w_rules" if kind == "rules" else f"w_{kind}:{quote(value, safe='')}"
                if len(callback.encode("utf-8")) > 64:
                    callback = "w_popup_long"
                built.append(InlineKeyboardButton(text, callback_data=callback))
        if built:
            keyboard.append(built)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


class CloneWelcomeEditorMixin:
    @staticmethod
    def welcome_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Text",callback_data="a_welcome_text"), InlineKeyboardButton("👀 See",callback_data="a_welcome_see_text")],
            [InlineKeyboardButton("🖼 Media",callback_data="a_welcome_media"), InlineKeyboardButton("👀 See",callback_data="a_welcome_see_media")],
            [InlineKeyboardButton("🔗 Buttons",callback_data="a_welcome_buttons"), InlineKeyboardButton("👀 See",callback_data="a_welcome_see_buttons")],
            [InlineKeyboardButton("👀 Full Preview",callback_data="a_welcome_preview")],
            [InlineKeyboardButton("⬅ Back",callback_data="a_settings")],
        ])

    @staticmethod
    def welcome_text_menu(has_text=False):
        rows=[]
        if has_text:
            rows.append([InlineKeyboardButton("🗑 Remove Text",callback_data="a_welcome_remove_text")])
        rows.append([InlineKeyboardButton("⬅ Back",callback_data="a_welcome")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def welcome_media_menu(has_media=False):
        rows=[]
        if has_media:
            rows.append([InlineKeyboardButton("🗑 Remove Media",callback_data="a_welcome_remove_media")])
        rows.append([InlineKeyboardButton("⬅ Back",callback_data="a_welcome")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def welcome_buttons_menu(has_buttons=False):
        rows=[]
        if has_buttons:
            rows.append([InlineKeyboardButton("🚫 Remove Keyboard",callback_data="a_welcome_remove_buttons")])
        rows.append([InlineKeyboardButton("⬅ Back",callback_data="a_welcome")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def welcome_quick_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Plans",callback_data="a_wq_plans"),InlineKeyboardButton("💳 Buy",callback_data="a_wq_buy")],
            [InlineKeyboardButton("👤 My Profile",callback_data="a_wq_profile"),InlineKeyboardButton("🔄 Renew",callback_data="a_wq_renew")],
            [InlineKeyboardButton("🎁 Referral",callback_data="a_wq_referral"),InlineKeyboardButton("🔓 Referral Unlock",callback_data="a_wq_referral_unlock")],
            [InlineKeyboardButton("📞 Support",callback_data="a_wq_support"),InlineKeyboardButton("🏠 Main Menu",callback_data="a_wq_home")],
            [InlineKeyboardButton("⬅ Back",callback_data="a_welcome_buttons")],
        ])

    @staticmethod
    def personalize(text,user,bot_name="Subscription Bot",group_name=""):
        from datetime import datetime as _datetime
        now=_datetime.now()
        values={
            "{ID}":str(user.id),
            "{NAME}":user.first_name or "",
            "{SURNAME}":user.last_name or "",
            "{NAMESURNAME}":" ".join(x for x in [user.first_name,user.last_name] if x),
            "{USERNAME}":("@"+user.username) if user.username else "",
            "{LANG}":user.language_code or "",
            "{DATE}":now.strftime("%d-%m-%Y"),
            "{TIME}":now.strftime("%I:%M %p"),
            "{WEEKDAY}":now.strftime("%A"),
            "{MENTION}":user.mention_html(),
            "{BOTNAME}":bot_name,
        }
        result=text or ""
        for key,value in values.items(): result=result.replace(key,value)
        return result

    @staticmethod
    def parse_welcome_buttons(text):
        return parse_welcome_buttons(text)

    @staticmethod
    def build_welcome_keyboard(rows):
        return build_welcome_keyboard(rows)

    async def send_welcome(self,message,context,settings,user):
        # Seller ka editable welcome text optional hai. Agar seller text remove
        # kare, tab bhi default welcome title aur permanent SaaS branding dikhegi.
        seller_text=(settings.get("welcome_message") or "").strip()
        if seller_text:
            welcome_text=self.personalize(
                seller_text,
                user,
                settings.get("bot_name","Subscription Bot"),
                settings.get("group_name") or settings.get("welcome_group_name") or "",
            )
        else:
            welcome_text="👋 WELCOME TO OUR SUBSCRIPTION BOT"

        # Platform branding is controlled only from the Owner Dashboard.
        text=await append_branding(welcome_text)

        # Seller ke welcome buttons fully removable hain. Empty list ka matlab
        # welcome message ke niche koi button nahi dikhana.
        keyboard=self.build_welcome_keyboard(
            settings.get("welcome_buttons") or []
        )
        media_type=settings.get("welcome_media_type")
        file_id=settings.get("welcome_media_file_id")

        async def send(parse_mode="HTML"):
            kwargs={"reply_markup":keyboard}
            if parse_mode:
                kwargs["parse_mode"]=parse_mode
            if file_id and media_type=="photo":
                return await message.reply_photo(file_id,caption=text,**kwargs)
            if file_id and media_type=="video":
                return await message.reply_video(file_id,caption=text,**kwargs)
            if file_id and media_type=="animation":
                return await message.reply_animation(file_id,caption=text,**kwargs)
            if file_id and media_type=="document":
                return await message.reply_document(file_id,caption=text,**kwargs)
            return await message.reply_text(
                text,
                disable_web_page_preview=True,
                **kwargs,
            )

        try:
            return await send("HTML")
        except BadRequest as exc:
            logger.warning("Welcome HTML/media send failed; retrying plain text: %s",exc)
            try:
                return await send(None)
            except BadRequest:
                # If an old/invalid Telegram file_id is stored, remove media and send text.
                if file_id:
                    await set_seller_setting(self.owner(context),"welcome_media_type","")
                    await set_seller_setting(self.owner(context),"welcome_media_file_id","")
                    settings["welcome_media_type"]=""
                    settings["welcome_media_file_id"]=""
                    return await message.reply_text(
                        text,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                raise

