from src.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT scoring_version, COUNT(*)
            FROM lead_scores
            WHERE is_current = TRUE
            GROUP BY scoring_version
            ORDER BY scoring_version DESC
        """)
        for row in cur.fetchall():
            print(row)
