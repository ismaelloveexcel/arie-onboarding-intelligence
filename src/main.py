import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from pythonjsonlogger import jsonlogger

from src.config import APP_ENV, LOG_LEVEL
from src.db import check_connection
from src.scoring import SCORING_VERSION

# --- Logging setup ---
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.handlers = [handler]
root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

logger = logging.getLogger(__name__)

app = FastAPI(title="Arie Leads", docs_url=None if APP_ENV == "production" else "/docs")


@app.get("/health")
def health(response: Response):
    db_ok = check_connection()

    queue_rows = 0
    queue_refreshed_at = None
    queue_fresh = False

    if db_ok:
        try:
            from src.db import get_conn

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*), MAX(refreshed_at) FROM queue_snapshot"
                    )
                    row = cur.fetchone()
                    if row:
                        queue_rows = row[0] or 0
                        queue_refreshed_at = row[1]
                        if queue_refreshed_at:
                            age = datetime.now(timezone.utc) - queue_refreshed_at.replace(
                                tzinfo=timezone.utc
                            ) if queue_refreshed_at.tzinfo is None else datetime.now(timezone.utc) - queue_refreshed_at
                            queue_fresh = age.total_seconds() < 25 * 3600
        except Exception as exc:
            logger.warning("health_queue_check_failed", extra={"error": str(exc)})

    if not db_ok:
        response.status_code = 503

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "connected" if db_ok else "unreachable",
        "queue_rows": queue_rows,
        "queue_refreshed_at": queue_refreshed_at.isoformat() if queue_refreshed_at else None,
        "queue_fresh": queue_fresh,
        "scoring_version": SCORING_VERSION,
    }
