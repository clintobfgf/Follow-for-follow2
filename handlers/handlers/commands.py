"""
handlers/commands.py
---------------------
Session lifecycle commands: /open, /close, /track, /repoint, /endsec.
"""

import logging
from datetime import datetime

from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes

from database import get_db
from models import Session, Participant, Report, Warning, Ban
from utils import require_admin

logger = logging.getLogger(__name__)


async def open_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/open - Start a new F4F session in this group and unlock chat."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat

    with get_db() as db:
        existing = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status.in_(["open", "closed"]))
            .first()
        )
        if existing:
            await update.effective_message.reply_text(
                "⚠ A session is already active in this group. Use /close or /endsec "
                "to finish it before opening a new one."
            )
            return

        new_session = Session(chat_id=chat.id, status="open")
        db.add(new_session)
        logger.info("Session opened in chat %s", chat.id)

    try:
        await context.bot.set_chat_permissions(
            chat.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception:
        logger.exception("Failed to unlock chat %s permissions.", chat.id)

    await update.effective_message.reply_text(
        "🟢 F4F SESSION OPEN\n\n"
        "Drop your X profile link.\n\n"
        "Example:\n"
        "https://x.com/username\n\n"
        "Only one submission is allowed.\n\n"
        "After following everyone, report by sending:\n\n"
        "ad\n\n"
        "Pin this message."
    )


async def close_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/close - Stop accepting X links, lock group, build the tracking list."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat

    with get_db() as db:
        session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "open")
            .first()
        )
        if not session:
            await update.effective_message.reply_text("⚠ No open session to close in this group.")
            return

        session.status = "closed"
        session.closed_at = datetime.utcnow()

        participants = (
            db.query(Participant).filter(Participant.session_id == session.id).all()
        )

        for p in participants:
            existing_report = (
                db.query(Report)
                .filter(Report.telegram_id == p.telegram_id, Report.session_id == session.id)
                .first()
            )
            if not existing_report:
                db.add(Report(telegram_id=p.telegram_id, session_id=session.id, reported=False))

        participant_count = len(participants)
        logger.info("Session %s closed in chat %s with %s participants", session.id, chat.id, participant_count)

    try:
        await context.bot.set_chat_permissions(
            chat.id,
            permissions=ChatPermissions(can_send_messages=True),
        )
    except Exception:
        logger.exception("Failed to lock chat %s permissions.", chat.id)

    await update.effective_message.reply_text(
        f"🔒 SESSION CLOSED\n\n"
        f"Participants: {participant_count}\n\n"
        f"Tracking has started."
    )


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/track - Show the full tracking table for the active session."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat

    with get_db() as db:
        session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "closed")
            .first()
        )
        if not session:
            await update.effective_message.reply_text(
                "⚠ No closed session is currently being tracked in this group."
            )
            return

        participants = (
            db.query(Participant).filter(Participant.session_id == session.id).all()
        )
        reports = {
            r.telegram_id: r
            for r in db.query(Report).filter(Report.session_id == session.id).all()
        }

        reported_count = sum(1 for r in reports.values() if r.reported)
        waiting_count = len(participants) - reported_count

        lines = [
            f"Participants: {len(participants)}",
            f"✅ Reported: {reported_count}",
            f"❌ Waiting: {waiting_count}",
            "",
        ]

        for p in participants:
            r = reports.get(p.telegram_id)
            status = "✅ Reported" if (r and r.reported) else "❌ Waiting"
            report_time = r.reported_at.strftime("%Y-%m-%d %H:%M") if (r and r.reported_at) else "-"
            tg_username = f"@{p.telegram_username}" if p.telegram_username else "(no username)"

            lines.append(
                f"Telegram: {tg_username}\n"
                f"Name: {p.display_name}\n"
                f"X: @{p.x_username}\n"
                f"Status: {status}\n"
                f"Submitted: {p.submitted_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"Reported: {report_time}\n"
            )

    text = "\n".join(lines)

    for chunk_start in range(0, len(text), 3800):
        await update.effective_message.reply_text(text[chunk_start:chunk_start + 3800])


async def repoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/repoint - List only participants who have not yet reported."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat

    with get_db() as db:
        session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "closed")
            .first()
        )
        if not session:
            await update.effective_message.reply_text(
                "⚠ No closed session is currently being tracked in this group."
            )
            return

        participants = (
            db.query(Participant).filter(Participant.session_id == session.id).all()
        )
        reported_ids = {
            r.telegram_id
            for r in db.query(Report)
            .filter(Report.session_id == session.id, Report.reported == True)  # noqa: E712
            .all()
        }

        waiting = [p for p in participants if p.telegram_id not in reported_ids]

    if not waiting:
        await update.effective_message.reply_text("✅ Everyone has reported. Nothing to show.")
        return

    lines = ["⚠ Waiting for report", ""]
    for i, p in enumerate(waiting, start=1):
        tg_username = f"@{p.telegram_username}" if p.telegram_username else "(no username)"
        lines.append(f"{i}.\n\nTelegram:\n{tg_username}\n\nX:\n@{p.x_username}\n")

    text = "\n".join(lines)
    for chunk_start in range(0, len(text), 3800):
        await update.effective_message.reply_text(text[chunk_start:chunk_start + 3800])


async def endsec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/endsec - Ban everyone who never reported, then archive the session."""
    if not await require_admin(update, context):
        return

    chat = update.effective_chat

    with get_db() as db:
        session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "closed")
            .first()
        )
        if not session:
            await update.effective_message.reply_text(
                "⚠ No closed session is currently being tracked in this group."
            )
            return

        participants = (
            db.query(Participant).filter(Participant.session_id == session.id).all()
        )
        reported_ids = {
            r.telegram_id
            for r in db.query(Report)
            .filter(Report.session_id == session.id, Report.reported == True)  # noqa: E712
            .all()
        }

        not_reported = [p for p in participants if p.telegram_id not in reported_ids]

        banned_usernames = []
        for p in not_reported:
            try:
                await context.bot.ban_chat_member(chat.id, p.telegram_id)
                banned_usernames.append(f"@{p.telegram_username}" if p.telegram_username else str(p.telegram_id))
                db.add(Ban(telegram_id=p.telegram_id, chat_id=chat.id, reason="Did not report F4F session"))
                logger.info("Banned %s from chat %s for not reporting.", p.telegram_id, chat.id)
            except Exception:
                logger.exception("Failed to ban participant %s in chat %s", p.telegram_id, chat.id)

        session.status = "archived"

        participant_count = len(participants)
        reported_count = len(reported_ids)
        banned_count = len(banned_usernames)

    lines = [
        "SESSION ENDED",
        "",
        f"Participants: {participant_count}",
        f"Reported: {reported_count}",
        f"Banned: {banned_count}",
    ]
    if banned_usernames:
        lines.append("")
        lines.append("Banned users:")
        lines.extend(banned_usernames)

    await update.effective_message.reply_text("\n".join(lines))
