from __future__ import annotations

from typing import Any, Iterable
import re
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from telegram import CopyTextButton
except Exception:  # compatibility fallback
    CopyTextButton = None


def group_buttons_header() -> str:
    return (
        "👉 Set the buttons to be placed under the message\n"
        "Send a message structured as follows:\n\n"
        "• Add a single button:\n"
        "Button title - t.me/LinkExample\n\n"
        "• Add multiple buttons on a single line:\n"
        "Button title - t.me/LinkExample && Button text - t.me/LinkExample\n\n"
        "• Add multiple rows of buttons:\n"
        "Button title - t.me/LinkExample\n"
        "Button title - t.me/LinkExample\n\n"
        "Special buttons\n\n"
        "• Add a button that shows a popup:\n"
        "Button title - popup: Popup text\n"
        "or\n"
        "Button title - alert: Popup text\n\n"
        "• Add a button with a link to the group rules:\n"
        "Button title - rules\n\n"
        "• Add a share button:\n"
        "Button title - share: Text to be shared\n\n"
        "• Add a button with copyable text:\n"
        "Button title - copy: Text copied on click"
    )


def parse_group_buttons(text: str) -> list[list[dict[str, str]]]:
    """Parse group-manager button syntax with precise, user-friendly errors.

    The separator is a hyphen, with optional spaces around it. Therefore all
    of these are accepted:
      Button - target
      Button- target
      Button -target
      Button-target
    """
    rows: list[list[dict[str, str]]] = []

    def fail(line_no: int, button_no: int, message: str, raw: str = "") -> None:
        where = f"Line {line_no}, button {button_no}"
        if raw:
            where += f' ("{raw[:80]}")'
        raise ValueError(f"{where}: {message}")

    for line_no, raw_line in enumerate((text or "").splitlines(), 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        row: list[dict[str, str]] = []
        chunks = raw_line.split("&&")
        for button_no, chunk in enumerate(chunks, 1):
            chunk = chunk.strip()
            if not chunk:
                fail(
                    line_no, button_no,
                    "Empty button. Remove the extra '&&' or add a button."
                )

            # Spaces around '-' are optional. This fixes inputs such as
            # "UNLOCK -share:" / "UNLOCK-share:" while still requiring a
            # separator between title and target.
            match = re.match(r"^(.*?)\s*-\s*(.+)$", chunk, flags=re.DOTALL)
            if not match:
                fail(
                    line_no, button_no,
                    "Missing '-' separator. Use: Button title - target"
                )

            title = match.group(1).strip()
            target = match.group(2).strip()
            if not title:
                fail(line_no, button_no, "Button title is missing. Use: Button title - target", chunk)
            if not target:
                fail(line_no, button_no, "Button target is missing after '-'.", chunk)

            lower = target.casefold()

            if target.startswith(("http://", "https://", "tg://")) or lower.startswith("t.me/"):
                if lower.startswith("t.me/"):
                    target = "https://" + target
                row.append({"text": title, "type": "url", "value": target})
                continue

            if target.startswith("@"):
                username = target[1:].strip()
                if (
                    not username
                    or len(username) > 32
                    or not all(ch.isalnum() or ch == "_" for ch in username)
                ):
                    fail(
                        line_no, button_no,
                        "Invalid Telegram username. Use: Button title - @username",
                        chunk,
                    )
                row.append({
                    "text": title,
                    "type": "url",
                    "value": f"https://t.me/{username}",
                })
                continue

            if lower.startswith("popup:"):
                value = target.split(":", 1)[1].strip()
                if not value:
                    fail(line_no, button_no, "Popup text is missing. Use: Button title - popup: Popup text", chunk)
                row.append({"text": title, "type": "popup", "value": value})
                continue

            if lower.startswith("alert:"):
                value = target.split(":", 1)[1].strip()
                if not value:
                    fail(line_no, button_no, "Alert text is missing. Use: Button title - alert: Alert text", chunk)
                row.append({"text": title, "type": "alert", "value": value})
                continue

            if lower == "rules":
                row.append({"text": title, "type": "rules", "value": "rules"})
                continue

            if lower.startswith("share:"):
                value = target.split(":", 1)[1].strip()
                if not value:
                    fail(line_no, button_no, "Share text is missing. Use: Button title - share: Text to share", chunk)
                row.append({"text": title, "type": "share", "value": value})
                continue

            if lower.startswith("copy:"):
                value = target.split(":", 1)[1].strip()
                if not value:
                    fail(line_no, button_no, "Copy text is missing. Use: Button title - copy: Text to copy", chunk)
                row.append({"text": title, "type": "copy", "value": value})
                continue

            fail(
                line_no, button_no,
                "Unknown target. Use URL, @username, popup:, alert:, rules, share:, or copy:.",
                chunk,
            )

        if row:
            rows.append(row)

    if not rows:
        raise ValueError("No buttons found. Send at least one button using: Button title - target")
    return rows


def build_group_keyboard(
    rows: Iterable[Iterable[dict[str, Any]]] | None,
    *,
    item_key: str,
    preview_group_id: int | None = None,
) -> InlineKeyboardMarkup | None:
    if not rows:
        return None

    keyboard: list[list[InlineKeyboardButton]] = []
    for row_index, row in enumerate(rows):
        built: list[InlineKeyboardButton] = []
        for col_index, item in enumerate(row):
            title = str(item.get("text") or "Button")
            typ = str(item.get("type") or "url")
            value = str(item.get("value") or "")

            if typ == "url":
                if value:
                    built.append(InlineKeyboardButton(title, url=value))
                continue

            if typ == "share":
                share_url = "https://t.me/share/url?url=&text=" + quote(value)
                built.append(InlineKeyboardButton(title, url=share_url))
                continue

            if typ == "copy" and CopyTextButton is not None:
                try:
                    built.append(
                        InlineKeyboardButton(
                            title,
                            copy_text=CopyTextButton(text=value),
                        )
                    )
                    continue
                except Exception:
                    pass

            # popup / alert / rules / copy fallback use callback query.
            if preview_group_id is None:
                callback = f"gmsp_{item_key}_{row_index}_{col_index}"
            else:
                callback = f"gmspv_{int(preview_group_id)}_{item_key}_{row_index}_{col_index}"
            built.append(InlineKeyboardButton(title, callback_data=callback[:64]))

        if built:
            keyboard.append(built)

    return InlineKeyboardMarkup(keyboard) if keyboard else None


def find_button(item: dict[str, Any], row_index: int, col_index: int) -> dict[str, Any] | None:
    rows = item.get("buttons") or []
    try:
        return rows[int(row_index)][int(col_index)]
    except (IndexError, TypeError, ValueError):
        return None
