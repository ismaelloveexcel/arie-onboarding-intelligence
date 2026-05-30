import datetime
import os
import pathlib

import psycopg

ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
out_dir = pathlib.Path("backups") / f"snapshot-{ts}"
out_dir.mkdir(parents=True, exist_ok=True)

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
        """)
        tables = [r[0] for r in cur.fetchall()]

    print(f"Snapshotting {len(tables)} tables -> {out_dir}")
    for t in tables:
        path = out_dir / f"{t}.csv"
        with conn.cursor() as cur, open(path, "w", encoding="utf-8", newline="") as f:
            with cur.copy(f"COPY (SELECT * FROM {t}) TO STDOUT WITH CSV HEADER") as cp:
                for chunk in cp:
                    f.write(chunk.tobytes().decode("utf-8"))
        size = path.stat().st_size
        print(f"  {t:35s} {size:>10,} bytes")

# Manifest
manifest = out_dir / "MANIFEST.txt"
manifest.write_text(
    f"Arie Leads Intelligence — DB snapshot\n"
    f"Taken: {datetime.datetime.utcnow().isoformat()}Z\n"
    f"Tables: {len(tables)}\n"
    f"Git tag: v1.0-baseline\n"
    f"Scoring version: 2025.1.3\n"
)
print(f"\nDone: {out_dir}")
