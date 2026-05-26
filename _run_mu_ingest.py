import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from src.db import get_conn
from src.ingestion.mauritius import fetch_mauritius_incorporations

with get_conn() as conn:
    n = fetch_mauritius_incorporations(conn)
    print("UPSERTED_MAURITIUS", n)
    cur = conn.cursor()
    cur.execute(
        "SELECT entity_type, COUNT(*) FROM companies "
        "WHERE jurisdiction='Mauritius' GROUP BY entity_type ORDER BY 2 DESC"
    )
    print("ENTITY_TYPES", list(cur))
    cur.execute(
        "SELECT MAX(incorporation_date), MIN(incorporation_date), COUNT(*) "
        "FROM companies WHERE jurisdiction='Mauritius'"
    )
    print("RANGE", list(cur))
