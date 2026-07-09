"""
handlers/tracking.py
---------------------
Core F4F tracking logic: routes group messages through moderation, then
X-link submission handling (open session) or "ad" report tracking (closed
session).
"""

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from database import get_db
from models import Session, Participant, Report
from utils import extract_x_username, display_name_for
from handlers.filters import moderate_message

logger = logging.getLogger(__name__)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main group message router: moderation -> submission -> report tracking."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None or chat is None or user is None:
        return

    if user.is_bot:
        return

    deleted = await moderate_message(update, context)
    if deleted:
        return

    text = (message.text or "").strip()
    if not text:
        return

    with get_db() as db:
        open_session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "open")
            .first()
        )
        closed_session = (
            db.query(Session)
            .filter(Session.chat_id == chat.id, Session.status == "closed")
            .first()
        )

        if open_session:
            await _handle_submission(update, db, open_session, text)
        elif closed_session:
            await _handle_report(update, db, closed_session, text)


async def _handle_submission(update: Update, db, session: Session, text: str) -> None:
    """Validate and store an X profile link submitted during an open session."""
    message = update.effective_message
    user = update.effective_user

    x_username = extract_x_username(text)

    if x_username is None:
        if text.startswith("http") or "://" in text:
            try:
                await message.delete()
            except Exception:
                logger.exception("Failed to delete invalid link submission.")
            await message.reply_text(
                "❌ That doesn't look like a valid X profile link. "
                "Please send it like: https://x.com/username"
            )
        return

    existing = (
        db.query(Participant)
        .filter(Participant.telegram_id == user.id, Participant.session_id == session.id)
        .first()
    )
    if existing:
        await message.reply_text("You have already submitted your X account.")
        return

    participant = Participant(
        telegram_id=user.id,
        telegram_username=user.username,
        display_name=display_name_for(user),
        x_username=x_username,
        x_link=text,
        submitted_at=datetime.utcnow(),
        session_id=session.id,
    )
    db.add(participant)
    logger.info("Participant %s (@%s) submitted X link in chat %s", user.id, x_username, message.chat_id)

    await message.reply_text(f"✅ Got it! Registered @{x_username} for this session.")


async def _handle_report(update: Update, db, session: Session, text: str) -> None:
    """Check if the message is the exact report keyword 'ad' and mark it."""
    message = update.effective_message
    user = update.effective_user

    if text.strip().lower() != "ad":
        return

    participant = (
        db.query(Participant)
        .filter(Participant.telegram_id == user.id, Participant.session_id == session.id)
        .first()
    )
    if not participant:
        return

    report = (
        db.query(Report)
        .filter(Report.telegram_id == user.id, Report.session_id == session.id)
        .first()
    )

    if report and report.reported:
        return

    if not report:
        report = Report(telegram_id=user.id, session_id=session.id, reported=False)
        db.add(report)

    report.reported = True
    report.reported_at = datetime.utcnow()
    logger.info("Participant %s reported in chat %s (session %s)", user.id, message.chat_id, session.id)

    await message.reply_text("✅ Report received.")
