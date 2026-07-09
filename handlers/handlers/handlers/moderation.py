"""
handlers/moderation.py
------------------------
Manual moderation commands: /ban, /unban, /mute, /unmute, /warn, /kick.
"""

import logging
from datetime import datetime, timedelta

from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes

from database import get_db
from models import Warning, Ban
from utils import require_admin, format_duration_to_seconds
from config import config

logger = logging.getLogger(__name__)


async def _resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Resolve the target user either from a replied-to message, or from the
    first command argument (a numeric Telegram ID; @usernames can't be
    resolved to an ID reliably without the user having messaged the bot
    before, so replying to their message is the recommended approach).
    """
    message = update.effective_message

    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    if context.args:
        arg = context.args[0].lstrip("@")
        if arg.isdigit():
            try:
                member = await context.bot.get_chat_member(update.effective_chat.id, int(arg))
                return member.user
            except Exception:
                logger.exception("Failed to resolve user id %s", arg)
                return None

    return None


async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ban @username (or reply to a message) - permanently remove a user."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    target = await _resolve_target_user(update, context)
    if not target:
        await update.effective_message.reply_text(
            "Usage: reply to the user's message with /ban, or /ban <telegram_id>"
        )
        return

    try:
        await context.bot.ban_chat_member(chat.id, target.id)
    except Exception:
        logger.exception("Failed to ban user %s in chat %s", target.id, chat.id)
        await update.effective_message.reply_text("⚠ Failed to ban that user (check bot admin rights).")
        return

    with get_db() as db:
        db.add(Ban(telegram_id=target.id, chat_id=chat.id, reason="Manual ban by admin"))

    logger.info("Admin banned user %s in chat %s", target.id, chat.id)
    await update.effective_message.reply_text(f"🔨 Banned {target.mention_html()}", parse_mode="HTML")


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unban <telegram_id> - lift a ban."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /unban <telegram_id>")
        return

    target_id = int(context.args[0])
    try:
        await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
    except Exception:
        logger.exception("Failed to unban user %s in chat %s", target_id, chat.id)
        await update.effective_message.reply_text("⚠ Failed to unban that user.")
        return

    logger.info("Admin unbanned user %s in chat %s", target_id, chat.id)
    await update.effective_message.reply_text(f"✅ Unbanned user {target_id}")


async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mute @username 30m - restrict a user from sending messages for a duration."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    message = update.effective_message

    duration_str = context.args[-1] if context.args else None
    duration_seconds = format_duration_to_seconds(duration_str) if duration_str else None

    target = await _resolve_target_user(update, context)
    if not target or duration_seconds is None:
        await message.reply_text("Usage: reply to the user with /mute 30m  (units: s, m, h, d)")
        return

    until_date = datetime.utcnow() + timedelta(seconds=duration_seconds)

    try:
        await context.bot.restrict_chat_member(
            chat.id,
            target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date,
        )
    except Exception:
        logger.exception("Failed to mute user %s in chat %s", target.id, chat.id)
        await message.reply_text("⚠ Failed to mute that user (check bot admin rights).")
        return

    logger.info("Admin muted user %s in chat %s for %ss", target.id, chat.id, duration_seconds)
    await message.reply_text(f"🔇 Muted {target.mention_html()} for {duration_str}", parse_mode="HTML")


async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unmute @username - restore a user's ability to send messages."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    message = update.effective_message

    target = await _resolve_target_user(update, context)
    if not target:
        await message.reply_text("Usage: reply to the user's message with /unmute")
        return

    try:
        await context.bot.restrict_chat_member(
            chat.id,
            target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception:
        logger.exception("Failed to unmute user %s in chat %s", target.id, chat.id)
        await message.reply_text("⚠ Failed to unmute that user.")
        return

    logger.info("Admin unmuted user %s in chat %s", target.id, chat.id)
    await message.reply_text(f"🔊 Unmuted {target.mention_html()}", parse_mode="HTML")


async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/warn @username - issue a warning; auto-kick after MAX_WARNINGS."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    message = update.effective_message

    target = await _resolve_target_user(update, context)
    if not target:
        await message.reply_text("Usage: reply to the user's message with /warn")
        return

    with get_db() as db:
        warning = (
            db.query(Warning)
            .filter(Warning.telegram_id == target.id, Warning.chat_id == chat.id)
            .first()
        )
        if not warning:
            warning = Warning(telegram_id=target.id, chat_id=chat.id, count=0)
            db.add(warning)

        warning.count += 1
        current_count = warning.count

    logger.info("Admin warned user %s in chat %s (count=%s)", target.id, chat.id, current_count)

    if current_count >= config.MAX_WARNINGS:
        try:
            await context.bot.ban_chat_member(chat.id, target.id)
            await context.bot.unban_chat_member(chat.id, target.id)  # kick, not permanent ban
        except Exception:
            logger.exception("Failed to auto-kick user %s in chat %s after max warnings", target.id, chat.id)

        await message.reply_text(
            f"⚠ {target.mention_html()} reached {current_count} warnings and has been kicked.",
            parse_mode="HTML",
        )
    else:
        await message.reply_text(
            f"⚠ Warned {target.mention_html()} ({current_count}/{config.MAX_WARNINGS})",
            parse_mode="HTML",
        )


async def kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kick @username - remove a user, allowing them to rejoin later."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat
    message = update.effective_message

    target = await _resolve_target_user(update, context)
    if not target:
        await message.reply_text("Usage: reply to the user's message with /kick")
        return

    try:
        await context.bot.ban_chat_member(chat.id, target.id)
        await context.bot.unban_chat_member(chat.id, target.id)
    except Exception:
        logger.exception("Failed to kick user %s in chat %s", target.id, chat.id)
        await message.reply_text("⚠ Failed to kick that user (check bot admin rights).")
        return

    logger.info("Admin kicked user %s in chat %s", target.id, chat.id)
    await message.reply_text(f"👢 Kicked {target.mention_html()}", parse_mode="HTML")
