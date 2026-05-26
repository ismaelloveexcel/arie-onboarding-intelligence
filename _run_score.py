import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from src.db import get_conn
from src.pipeline import _score_new_companies

with get_conn() as conn:
    n = _score_new_companies(conn)
    print("SCORED", n)
    cur = conn.cursor()
    cur.execute(
        "SELECT c.jurisdiction, COUNT(*) FROM companies c "
        "JOIN lead_scores ls ON ls.company_id=c.id AND ls.is_current "
        "GROUP BY c.jurisdiction ORDER BY 2 DESC"
    )
    print("CURRENT_SCORED_BY_JX", list(cur))
