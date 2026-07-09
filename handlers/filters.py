"""
handlers/filters.py
--------------------
Automatic message moderation: deletes Telegram invite links, disallowed
spam URLs, repeated/flooded messages, and forwarded advertisements.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from utils import (
    contains_telegram_invite,
    contains_disallowed_url,
    is_flooding,
    is_repeated_message,
    is_group_admin,
)

logger = logging.getLogger(__name__)


async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Apply automatic moderation filters. Returns True if the message was
    deleted, False if it should continue through the handler pipeline.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None or chat is None or user is None:
        return False

    if await is_group_admin(update, context):
        return False

    text = message.text or message.caption or ""

    if message.forward_origin is not None or message.forward_date is not None:
        await _delete_silently(message, reason="forwarded message")
        return True

    if contains_telegram_invite(text):
        await _delete_silently(message, reason="Telegram invite link")
        return True

    if contains_disallowed_url(text):
        await _delete_silently(message, reason="disallowed spam URL")
        return True

    if is_repeated_message(chat.id, user.id, text) and text.strip():
        await _delete_silently(message, reason="repeated message")
        return True

    if is_flooding(chat.id, user.id):
        await _delete_silently(message, reason="flooding")
        return True

    return False


async def _delete_silently(message, reason: str) -> None:
    """Attempt to delete a message, logging failures without crashing."""
    try:
        await message.delete()
        logger.info(
            "Deleted message from user %s in chat %s (%s)",
            message.from_user.id if message.from_user else "unknown",
            message.chat_id,
            reason,
        )
    except Exception:
        logger.exception("Failed to delete message (%s) in chat %s", reason, message.chat_id)
