from src.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                id,
                started_at,
                completed_at,
                status,
                duration_seconds,
                error
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT 5
        """)
        for row in cur.fetchall():
            print(row)
