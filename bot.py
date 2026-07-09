"""
bot.py
------
Main entry point for the F4F Tracker Bot.
"""

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import config
from database import init_db
from handlers.commands import open_session, close_session, track, repoint, endsec
from handlers.moderation import ban_user, unban_user, mute_user, unmute_user, warn_user, kick_user
from handlers.tracking import on_group_message


def setup_logging() -> None:
    """Configure root logging to write to console and logs/bot.log."""
    os.makedirs(config.LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(config.LOG_LEVEL)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log every unhandled exception without crashing the bot."""
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)


def start_keepalive_server() -> None:
    """
    Run a minimal Flask app in a background thread so free-tier hosts
    (Render/Railway/Koyeb) see an open HTTP port, and services like
    UptimeRobot can ping it to prevent the instance from sleeping.
    """
    from flask import Flask

    app = Flask(__name__)

    @app.route("/")
    def health():
        return "F4F Tracker Bot is running.", 200

    def run():
        app.run(host="0.0.0.0", port=config.KEEPALIVE_PORT)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info("Keep-alive server started on port %s", config.KEEPALIVE_PORT)


def build_application() -> Application:
    """Construct the PTB Application and register every handler."""
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("open", open_session))
    application.add_handler(CommandHandler("close", close_session))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("repoint", repoint))
    application.add_handler(CommandHandler("endsec", endsec))

    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler("warn", warn_user))
    application.add_handler(CommandHandler("kick", kick_user))

    # Forwarded messages are intentionally NOT excluded here — they are
    # still routed to on_group_message, which detects and deletes them via
    # handlers/filters.py (message.forward_origin check).
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
            on_group_message,
        )
    )

    application.add_error_handler(on_error)

    return application


def main() -> None:
    setup_logging()
    config.validate()
    init_db()

    if config.ENABLE_KEEPALIVE_SERVER:
        start_keepalive_server()

    application = build_application()

    logger.info("F4F Tracker Bot starting (polling mode)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

r
if __name__ == "__main__":
    main()
