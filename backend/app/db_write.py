"""Serialize SQLite writes and retry on 'database is locked'."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, TypeVar

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

WRITE_LOCK = threading.RLock()
T = TypeVar("T")

MAX_RETRIES = 8
RETRY_DELAY_SEC = 0.15


@contextmanager
def serialized_write():
    """Hold while performing DB commits that contend with background threads."""
    WRITE_LOCK.acquire()
    try:
        yield
    finally:
        WRITE_LOCK.release()


def run_write_with_retry(operation: Callable[[], T], *, label: str = "db write") -> T | None:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with serialized_write():
                return operation()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            delay = RETRY_DELAY_SEC * (attempt + 1)
            logger.debug("%s locked (attempt %s), retry in %.2fs", label, attempt + 1, delay)
            time.sleep(delay)
    if last_exc:
        logger.warning("%s failed after retries: %s", label, last_exc)
    return None


def commit_session(db: Session, *, label: str = "commit") -> bool:
    def _commit() -> bool:
        db.commit()
        return True

    return run_write_with_retry(_commit, label=label) is True
