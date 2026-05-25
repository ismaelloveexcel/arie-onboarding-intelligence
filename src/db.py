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


def upsert_company(conn, data: dict) -> str:
    """Insert or update a company row. Returns the UUID string."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO companies (
                source_system, source_ref, company_name, normalised_name,
                jurisdiction, entity_type, incorporation_date,
                registered_address, sic_codes, website, verify_url, raw_data
            ) VALUES (
                %(source_system)s, %(source_ref)s, %(company_name)s, %(normalised_name)s,
                %(jurisdiction)s, %(entity_type)s, %(incorporation_date)s,
                %(registered_address)s, %(sic_codes)s, %(website)s, %(verify_url)s,
                %(raw_data)s
            )
            ON CONFLICT (source_system, source_ref) DO UPDATE SET
                company_name      = EXCLUDED.company_name,
                normalised_name   = EXCLUDED.normalised_name,
                entity_type       = EXCLUDED.entity_type,
                incorporation_date= EXCLUDED.incorporation_date,
                registered_address= EXCLUDED.registered_address,
                sic_codes         = EXCLUDED.sic_codes,
                website           = EXCLUDED.website,
                verify_url        = EXCLUDED.verify_url,
                raw_data          = EXCLUDED.raw_data,
                updated_at        = NOW()
            RETURNING id
            """,
            data,
        )
        return str(cur.fetchone()[0])


def check_connection() -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.error("db_connection_failed", extra={"error": str(exc)})
        return False
