"""
utils.py
--------
Shared helper functions: admin verification, X link validation/parsing,
flood-control bookkeeping, and message formatting.
"""

import logging
import re
import time
from collections import defaultdict
from typing import Optional

from telegram import Update, Chat
from telegram.ext import ContextTypes

from config import config

logger = logging.getLogger(__name__)

X_LINK_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/?$"
)

TELEGRAM_INVITE_PATTERN = re.compile(r"(t\.me/(joinchat/|\+)|telegram\.me/joinchat)", re.IGNORECASE)

GENERIC_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

_flood_tracker: dict = defaultdict(list)
_last_message_cache: dict = {}


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Verify the command sender is the creator or an admin of THIS chat."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except Exception:
        logger.exception("Failed to fetch chat member status for admin check.")
        return False

    return member.status in ("creator", "administrator")


async def require_group(update: Update) -> bool:
    """Return True if this update originates from a group or supergroup."""
    chat = update.effective_chat
    return chat is not None and chat.type in (Chat.GROUP, Chat.SUPERGROUP)


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Combined guard for admin-only commands. Returns True if allowed to proceed."""
    if not await require_group(update):
        await update.effective_message.reply_text("This command only works inside a group.")
        return False

    if not await is_group_admin(update, context):
        await update.effective_message.reply_text("⛔ Admins only.")
        return False

    return True


def extract_x_username(text: str) -> Optional[str]:
    """Validate an X/Twitter profile link and return the extracted username."""
    text = text.strip()
    match = X_LINK_PATTERN.match(text)
    if not match:
        return None
    return match.group(4)


def contains_telegram_invite(text: str) -> bool:
    """Detect Telegram invite links, which are always disallowed."""
    return bool(TELEGRAM_INVITE_PATTERN.search(text))


def contains_disallowed_url(text: str) -> bool:
    """Detect any URL that isn't an X/Twitter profile link."""
    urls = GENERIC_URL_PATTERN.findall(text)
    if not urls:
        return False
    for url in urls:
        if not X_LINK_PATTERN.match(url.strip()):
            return True
    return False


def is_flooding(chat_id: int, user_id: int) -> bool:
    """Track message timestamps per (chat, user) in a sliding window."""
    key = (chat_id, user_id)
    now = time.time()
    window_start = now - config.FLOOD_WINDOW_SECONDS

    recent = [t for t in _flood_tracker[key] if t >= window_start]
    recent.append(now)
    _flood_tracker[key] = recent

    return len(recent) > config.FLOOD_MESSAGE_LIMIT


def is_repeated_message(chat_id: int, user_id: int, text: str) -> bool:
    """Detect if a user is sending the exact same message back-to-back."""
    key = (chat_id, user_id)
    last = _last_message_cache.get(key)
    _last_message_cache[key] = text
    return last is not None and last == text


def format_duration_to_seconds(duration_str: str) -> Optional[int]:
    """Convert a shorthand duration string like '30m', '2h', '1d' into seconds."""
    match = re.match(r"^(\d+)([smhd])$", duration_str.strip().lower())
    if not match:
        return None

    value, unit = int(match.group(1)), match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def display_name_for(user) -> str:
    """Build a readable display name from a Telegram User object."""
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)
