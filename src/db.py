import logging
from contextlib import contextmanager

import psycopg

from src.config import DATABASE_URL

logger = logging.getLogger(__name__)


@contextmanager
def get_conn():
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_connection() -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.error("db_connection_failed", extra={"error": str(exc)})
        return False
