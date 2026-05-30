from src.db import get_conn

QUERY = """
SELECT 'pscs' AS t, COUNT(*) AS n FROM company_pscs
UNION ALL
SELECT 'officers', COUNT(*) FROM company_officers
UNION ALL
SELECT 'enriched_companies', COUNT(*) FROM companies WHERE last_enriched_at IS NOT NULL
"""

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        for row in cur.fetchall():
            print(row)
