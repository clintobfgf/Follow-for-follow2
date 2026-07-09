"""
database.py
-----------
Database engine and session management for the F4F Tracker Bot.
"""

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import config
from models import Base

logger = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(config.DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized at %s", config.DATABASE_URL)


@contextmanager
def get_db():
    """
    Context manager yielding a SQLAlchemy session. Automatically rolls
    back on exception and always closes the session.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Database transaction failed, rolled back.")
        raise
    finally:
        db.close()
