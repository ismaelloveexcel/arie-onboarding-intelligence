import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from src.db import get_conn
from src.pipeline import _refresh_queue

with get_conn() as conn:
    n = _refresh_queue(conn)
    print("QUEUE_SNAPSHOT_ROWS", n)
    cur = conn.cursor()
    cur.execute(
        "SELECT jurisdiction, COUNT(*) FROM queue_snapshot "
        "GROUP BY jurisdiction ORDER BY 2 DESC"
    )
    print("BY_JX", list(cur))
