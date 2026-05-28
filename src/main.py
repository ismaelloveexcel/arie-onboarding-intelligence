import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from psycopg.types.json import Jsonb
from pythonjsonlogger import jsonlogger

from src.config import ACTOR_NAMES, APP_ENV, LOG_LEVEL, RM_NAMES, SECRET_KEY
from src.db import check_connection, get_conn
from src.ingestion.lei_backfill import backfill_lei_company_links
from src.scoring import SCORING_VERSION

# --- Logging setup ---
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.handlers = [handler]
root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

logger = logging.getLogger(__name__)

_ACTOR_COOKIE = "actor"
_ACTOR_MAX_AGE = 30 * 24 * 3600
_actor_signer = TimestampSigner(SECRET_KEY)


def _read_actor(request: Request) -> str:
    raw = request.cookies.get(_ACTOR_COOKIE)
    if not raw:
        return ""
    try:
        unsigned = _actor_signer.unsign(raw, max_age=_ACTOR_MAX_AGE)
    except SignatureExpired:
        logger.info("actor_cookie_expired")
        return ""
    except BadSignature:
        logger.warning("actor_cookie_bad_signature")
        return ""
    return unsigned.decode("utf-8", errors="replace")


def _time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "Never"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 120:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


app = FastAPI(
    title="Arie Leads",
    docs_url=None if APP_ENV == "production" else "/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")

from src.introducers import router as introducers_router  # noqa: E402
app.include_router(introducers_router)

_STATUSES = ["New", "Reviewing", "Qualified", "Not Relevant", "Deferred", "Contacted", "Onboarding", "Not Fit"]


def _format_entity_type(raw: str | None) -> str:
    if not raw:
        return "—"
    mapping = {
        "ltd": "Private Ltd",
        "limited": "Private Ltd",
        "private-limited-company": "Private Ltd",
        "private limited company": "Private Ltd",
        "plc": "PLC",
        "public-limited-company": "PLC",
        "llp": "LLP",
        "limited-liability-partnership": "LLP",
        "private-unlimited-company": "Unlimited",
        "private-limited-guarant-nsc": "Guarantee Co.",
        "registered-overseas-entity": "Overseas Entity",
        "uk-establishment": "UK Establishment",
    }
    return mapping.get(raw.lower().strip(), raw.title())


_UPLOAD_REQUIRED_COLUMNS = ["company_name", "jurisdiction"]
_UPLOAD_MAX_ROWS = 10000
_UPLOAD_MAX_BYTES = 10 * 1024 * 1024


def _build_query_string(params: dict) -> str:
    cleaned = {key: value for key, value in params.items() if value not in (None, "")}
    return urlencode(cleaned)


def _render_action_panel(lead_id: UUID, assigned_to: str, status: str, notes: str, contacted_at, follow_up_at, saved: bool = False) -> HTMLResponse:
    return HTMLResponse(
        f"""
        <div class="card" id="action-panel">
          <h2>RM Actions</h2>
          <form
            hx-post="/leads/{lead_id}/action"
            hx-target="#action-panel"
            hx-swap="outerHTML"
            style="display:flex; flex-direction:column; gap:.75rem"
          >
            <div>
              <label>Assign To</label>
              <select name="assigned_to">
                <option value="">— Unassigned —</option>
                {''.join(f'<option value="{escape(rm)}" {"selected" if rm == assigned_to else ""}>{escape(rm)}</option>' for rm in RM_NAMES)}
              </select>
            </div>
            <div>
              <label>Status</label>
              <select name="status">
                {''.join(f'<option value="{escape(item)}" {"selected" if item == status else ""}>{escape(item)}</option>' for item in _STATUSES)}
              </select>
            </div>
            <div>
              <label>Notes</label>
              <textarea name="notes">{escape(notes or "")}</textarea>
            </div>
            <div class="grid-2">
              <div>
                <label>Contacted At</label>
                <input type="date" name="contacted_at" value="{contacted_at.date().isoformat() if contacted_at else ""}">
              </div>
              <div>
                <label>Follow Up</label>
                <input type="date" name="follow_up_at" value="{follow_up_at.date().isoformat() if follow_up_at else ""}">
              </div>
            </div>
            <button type="submit" class="btn btn-primary">Save</button>
            {"<div style=\"color:#065f46; font-size:12px; margin-top:.25rem\">✓ Saved</div>" if saved else ""}
          </form>
        </div>
        """
    )


def _parse_upload_csv(file_bytes: bytes) -> tuple[list[dict], list[str], list[str]]:
    errors: list[str] = []
    columns: list[str] = []
    rows: list[dict] = []

    if len(file_bytes) > _UPLOAD_MAX_BYTES:
        return [], [], [f"File exceeds {_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit."]

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [], ["CSV must be UTF-8 encoded."]

    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    missing = [column for column in _UPLOAD_REQUIRED_COLUMNS if column not in columns]
    if missing:
        errors.append("Missing required columns: " + ", ".join(missing))

    if not columns:
        return [], [], errors + ["CSV header row is missing."]

    for index, row in enumerate(reader, start=1):
        if index > _UPLOAD_MAX_ROWS:
            errors.append(f"CSV exceeds {_UPLOAD_MAX_ROWS} row limit.")
            break

        cleaned = {key: (value or "").strip() for key, value in row.items() if key}
        if not cleaned.get("company_name"):
            errors.append(f"Row {index}: company_name is required.")
        if not cleaned.get("jurisdiction"):
            errors.append(f"Row {index}: jurisdiction is required.")
        rows.append(cleaned)

    return columns, rows, errors


@app.get("/health")
def health(response: Response):
    db_ok = check_connection()

    queue_rows = 0
    queue_refreshed_at = None
    queue_fresh = False
    mauritius_last_seen = None
    last_pipeline_run = None

    if db_ok:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*), MAX(refreshed_at) FROM queue_snapshot")
                    row = cur.fetchone()
                    if row:
                        queue_rows = row[0] or 0
                        queue_refreshed_at = row[1]
                        if queue_refreshed_at:
                            if queue_refreshed_at.tzinfo is None:
                                queue_refreshed_at = queue_refreshed_at.replace(tzinfo=timezone.utc)
                            age = datetime.now(timezone.utc) - queue_refreshed_at
                            queue_fresh = age.total_seconds() < 25 * 3600
                    cur.execute(
                        "SELECT MAX(updated_at) FROM companies WHERE source_system = 'mauritius_mns'"
                    )
                    mu_row = cur.fetchone()
                    if mu_row and mu_row[0] is not None:
                        ts = mu_row[0]
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        mauritius_last_seen = ts
                    cur.execute(
                        """
                        SELECT started_at, completed_at, status, uk_count, mu_count,
                               scores_count, queue_rows, duration_seconds, error
                        FROM pipeline_runs
                        ORDER BY started_at DESC
                        LIMIT 1
                        """
                    )
                    pr_row = cur.fetchone()
                    if pr_row:
                        started_at = pr_row[0]
                        completed_at = pr_row[1]
                        if started_at and started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=timezone.utc)
                        if completed_at and completed_at.tzinfo is None:
                            completed_at = completed_at.replace(tzinfo=timezone.utc)
                        last_pipeline_run = {
                            "started_at": started_at.isoformat() if started_at else None,
                            "completed_at": completed_at.isoformat() if completed_at else None,
                            "status": pr_row[2],
                            "uk_count": pr_row[3],
                            "mu_count": pr_row[4],
                            "scores_count": pr_row[5],
                            "queue_rows": pr_row[6],
                            "duration_seconds": float(pr_row[7]) if pr_row[7] is not None else None,
                            "error": pr_row[8],
                        }
        except Exception as exc:
            logger.warning("health_queue_check_failed", extra={"error": str(exc)})

    if not db_ok:
        response.status_code = 503
    elif not queue_fresh and queue_rows > 0:
        response.status_code = 503

    return {
        "status": "ok" if (db_ok and queue_fresh) else "degraded",
        "db": "connected" if db_ok else "unreachable",
        "queue_rows": queue_rows,
        "queue_refreshed_at": queue_refreshed_at.isoformat() if queue_refreshed_at else None,
        "queue_fresh": queue_fresh,
        "mauritius_last_seen": mauritius_last_seen.isoformat() if mauritius_last_seen else None,
        "last_pipeline_run": last_pipeline_run,
        "scoring_version": SCORING_VERSION,
    }


@app.post("/admin/lei-backfill")

@app.post("/admin/lei-backfill")
def admin_lei_backfill(request: Request):
    actor = _read_actor(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Authentication required")
    with get_conn() as conn:
        result = backfill_lei_company_links(conn)
    return result


@app.post("/me")
def set_actor(request: Request, actor: str = Form("")):
    raw_referer = request.headers.get("referer", "")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(raw_referer)
        same_origin = parsed.scheme in ("http", "https") and parsed.netloc == request.headers.get("host", "")
        redirect_to = raw_referer if same_origin else "/"
    except Exception:
        redirect_to = "/"
    response = RedirectResponse(url=redirect_to, status_code=303)
    if actor and actor in ACTOR_NAMES:
        signed = _actor_signer.sign(actor.encode("utf-8")).decode("ascii")
        response.set_cookie(
            _ACTOR_COOKIE,
            signed,
            max_age=_ACTOR_MAX_AGE,
            httponly=True,
            secure=(APP_ENV == "production"),
            samesite="lax",
        )
    elif not actor:
        response.delete_cookie(_ACTOR_COOKIE)
    # if actor provided but not in ACTOR_NAMES: silently ignore, don't set cookie
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    actor = _read_actor(request)
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=36)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Panel 1 — pipeline freshness
            cur.execute("""
                SELECT started_at, completed_at, status,
                       uk_count, mu_count, scores_count, queue_rows, duration_seconds
                FROM pipeline_runs ORDER BY started_at DESC LIMIT 1
            """)
            last_run_row = cur.fetchone()

            cur.execute("""
                SELECT started_at, uk_count, mu_count, scores_count, queue_rows
                FROM pipeline_runs WHERE status = 'success'
                ORDER BY started_at DESC LIMIT 1
            """)
            last_success_row = cur.fetchone()

            cur.execute(
                "SELECT MAX(last_enriched_at) FROM companies"
                " WHERE source_system = 'companies_house'"
            )
            last_enriched_at = cur.fetchone()[0]

            cur.execute("SELECT MAX(last_seen) FROM lei_records")
            last_lei_seen = cur.fetchone()[0]

            # Panel 2 — lead volume + scoring
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days')  AS last_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS last_30d
                FROM companies
            """)
            vol_row = cur.fetchone()

            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE score BETWEEN 0  AND 39)  AS s0_39,
                    COUNT(*) FILTER (WHERE score BETWEEN 40 AND 59)  AS s40_59,
                    COUNT(*) FILTER (WHERE score BETWEEN 60 AND 79)  AS s60_79,
                    COUNT(*) FILTER (WHERE score BETWEEN 80 AND 100) AS s80_100,
                    COUNT(*) AS total_scored
                FROM lead_scores WHERE is_current = TRUE
            """)
            score_row = cur.fetchone()

            # Panel 3 — enrichment coverage
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM companies WHERE source_system = 'companies_house')                      AS total_uk,
                    (SELECT COUNT(*) FROM companies WHERE source_system = 'companies_house'
                                                    AND last_enriched_at IS NOT NULL)                           AS enriched_uk,
                    (SELECT COUNT(DISTINCT company_id) FROM company_officers)                                    AS with_officers,
                    (SELECT COUNT(DISTINCT company_id) FROM company_pscs)                                        AS with_pscs,
                    (SELECT COUNT(*) FROM lei_records)                                                           AS total_lei,
                    (SELECT COUNT(*) FROM lei_records WHERE company_id IS NOT NULL)                              AS linked_lei,
                    (SELECT COUNT(*) FROM companies WHERE source_system = 'mauritius_mns')                       AS total_mu
            """)
            cov_row = cur.fetchone()

            # Panel 4 — queue + workflow
            cur.execute(
                "SELECT status, COUNT(*) AS cnt FROM rm_actions GROUP BY status ORDER BY cnt DESC"
            )
            status_counts = cur.fetchall()

            cur.execute("""
                SELECT i.company_name, COUNT(ia.id) AS lead_count
                FROM introducers i
                JOIN introducer_actions ia ON ia.introducer_id = i.id
                GROUP BY i.id, i.company_name
                ORDER BY lead_count DESC LIMIT 5
            """)
            top_introducers = cur.fetchall()

    def _ts(ts: datetime | None) -> datetime | None:
        if ts is None:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    def _pct(num: int, denom: int) -> float:
        return round(num * 100 / denom, 1) if denom else 0.0

    last_run_at = _ts(last_run_row[0]) if last_run_row else None
    last_enriched = _ts(last_enriched_at)
    last_lei = _ts(last_lei_seen)

    sources = [
        {
            "name": "Nightly Pipeline",
            "detail": "UK · Mauritius · Scoring",
            "last_run": _time_ago(last_run_at),
            "last_run_at": last_run_at,
            "status": last_run_row[2] if last_run_row else "—",
            "counts": (
                f"UK {last_success_row[1] or 0} · "
                f"MU {last_success_row[2] or 0} · "
                f"Scores {last_success_row[3] or 0}"
            ) if last_success_row else "—",
            "stale": last_run_at is None or last_run_at < stale_cutoff,
        },
        {
            "name": "CH Enrichment",
            "detail": "Officers · PSCs",
            "last_run": _time_ago(last_enriched),
            "last_run_at": last_enriched,
            "status": "enriched" if last_enriched else "—",
            "counts": "",
            "stale": last_enriched is None or last_enriched < stale_cutoff,
        },
        {
            "name": "LEI Backfill",
            "detail": "GLEIF linkage",
            "last_run": _time_ago(last_lei),
            "last_run_at": last_lei,
            "status": "linked" if last_lei else "—",
            "counts": "",
            "stale": last_lei is None or last_lei < stale_cutoff,
        },
    ]

    total_uk   = cov_row[0] if cov_row else 0
    enriched_uk = cov_row[1] if cov_row else 0
    with_officers = cov_row[2] if cov_row else 0
    with_pscs  = cov_row[3] if cov_row else 0
    total_lei  = cov_row[4] if cov_row else 0
    linked_lei = cov_row[5] if cov_row else 0
    total_mu   = cov_row[6] if cov_row else 0

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "actor": actor,
            # Panel 1
            "sources": sources,
            # Panel 2
            "total_leads":   vol_row[0] if vol_row else 0,
            "leads_7d":      vol_row[1] if vol_row else 0,
            "leads_30d":     vol_row[2] if vol_row else 0,
            "s0_39":         score_row[0] if score_row else 0,
            "s40_59":        score_row[1] if score_row else 0,
            "s60_79":        score_row[2] if score_row else 0,
            "s80_100":       score_row[3] if score_row else 0,
            "total_scored":  score_row[4] if score_row else 0,
            # Panel 3
            "total_uk":       total_uk,
            "pct_enriched_uk": _pct(enriched_uk, total_uk),
            "with_officers":  with_officers,
            "with_pscs":      with_pscs,
            "total_lei":      total_lei,
            "pct_lei_linked": _pct(linked_lei, total_lei),
            "total_mu":       total_mu,
            # Panel 4
            "status_counts":   status_counts,
            "top_introducers": top_introducers,
        }
    )


@app.get("/", response_class=HTMLResponse)
def queue(request: Request):
    filters = {
        "tier": request.query_params.get("tier", ""),
        "jurisdiction": request.query_params.get("jurisdiction", ""),
        "assigned_to": request.query_params.get("assigned_to", ""),
        "status": request.query_params.get("status", ""),
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "sort": request.query_params.get("sort", "date"),
    }
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    page_size = 50

    where_clauses = ["1=1"]
    params: list[object] = []
    if filters["tier"]:
        where_clauses.append("qs.tier = %s")
        params.append(filters["tier"])
    if filters["jurisdiction"]:
        where_clauses.append("c.jurisdiction = %s")
        params.append(filters["jurisdiction"])
    if filters["assigned_to"]:
        where_clauses.append("ra.assigned_to = %s")
        params.append(filters["assigned_to"])
    if filters["status"]:
        where_clauses.append("ra.status = %s")
        params.append(filters["status"])
    if filters["date_from"]:
        where_clauses.append("c.incorporation_date >= %s")
        params.append(filters["date_from"])
    if filters["date_to"]:
        where_clauses.append("c.incorporation_date <= %s")
        params.append(filters["date_to"])

    sort_sql = {
        "score": "qs.priority_score DESC, c.company_name ASC",
        "date": "c.incorporation_date DESC NULLS LAST, c.jurisdiction ASC, qs.priority_score DESC, c.company_name ASC",
        "date_asc": "c.incorporation_date ASC NULLS LAST, c.jurisdiction ASC, qs.priority_score DESC, c.company_name ASC",
        "name": "c.company_name ASC",
    }.get(filters["sort"], "c.incorporation_date DESC NULLS LAST, c.jurisdiction ASC, qs.priority_score DESC, c.company_name ASC")

    count_sql = f"""
        SELECT COUNT(*)
        FROM queue_snapshot qs
        JOIN companies c ON c.id = qs.canonical_company_id
        LEFT JOIN rm_actions ra ON ra.company_id = c.id
        WHERE {' AND '.join(where_clauses)}
    """
    query_sql = f"""
        SELECT
            c.id,
            c.company_name,
            c.jurisdiction,
            c.entity_type,
            c.incorporation_date,
            c.verify_url,
            qs.priority_score,
            qs.tier,
            qs.reason_summary,
            ra.assigned_to,
            ra.status,
            qs.refreshed_at
        FROM queue_snapshot qs
        JOIN companies c ON c.id = qs.canonical_company_id
        LEFT JOIN rm_actions ra ON ra.company_id = c.id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY {sort_sql}
        LIMIT %s OFFSET %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]
            cur.execute(query_sql, params + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()
            cur.execute("SELECT MAX(refreshed_at) FROM queue_snapshot")
            refreshed_at = cur.fetchone()[0]

    rendered_rows = [
        {
            "id": row[0],
            "company_name": row[1],
            "jurisdiction": row[2],
            "entity_type": _format_entity_type(row[3]),
            "incorporation_date": row[4],
            "verify_url": row[5],
            "priority_score": row[6],
            "tier": row[7],
            "reason_summary": row[8],
            "assigned_to": row[9],
            "status": row[10],
            "refreshed_at": row[11],
        }
        for row in rows
    ]
    total_pages = max((total + page_size - 1) // page_size, 1)
    query_params = {key: value for key, value in filters.items() if value}

    return templates.TemplateResponse(
        request=request,
        name="queue.html",
        context={
            "rows": rendered_rows,
            "total": total,
            "refreshed_at": f"{refreshed_at.day} {refreshed_at.strftime('%b %Y, %H:%M')} UTC" if refreshed_at else None,
            "filters": filters,
            "rm_names": RM_NAMES,
            "statuses": _STATUSES,
            "page": page,
            "total_pages": total_pages,
            "query_string": _build_query_string(query_params),
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: UUID):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                       c.incorporation_date, c.registered_address, c.source_system,
                       c.source_ref, c.verify_url,
                       ls.score, ls.tier, ls.reason_codes, ls.reason_summary, ls.scoring_version,
                       ra.assigned_to, ra.status, ra.notes, ra.contacted_at, ra.follow_up_at
                FROM companies c
                LEFT JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
                LEFT JOIN rm_actions ra ON ra.company_id = c.id
                WHERE c.id = %s
                """,
                (lead_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Lead not found")

            cur.execute(
                """
                SELECT entity_type, entity_id, action, actor, old_value, new_value, created_at
                FROM audit_log
                WHERE entity_type = 'company' AND entity_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (lead_id,),
            )
            audit_rows = cur.fetchall()

            cur.execute(
                """
                SELECT lei_code, entity_status, registration_status,
                       registered_on, managing_lou, gleif_url, last_seen
                FROM lei_records
                WHERE company_id = %s
                """,
                (lead_id,),
            )
            lei_row = cur.fetchone()

    score = {
        "score": row[9] if row[9] is not None else 0,
        "tier": row[10] if row[10] is not None else "LOW",
        "reason_codes": row[11] if row[11] is not None else [],
        "reason_summary": row[12] if row[12] is not None else "No signals matched.",
        "scoring_version": row[13] if row[13] is not None else SCORING_VERSION,
    }
    action = {
        "assigned_to": row[14],
        "status": row[15] or "New",
        "notes": row[16] or "",
        "contacted_at": row[17],
        "follow_up_at": row[18],
    }
    audit_rendered = [
        {
            "entity_type": item[0],
            "entity_id": item[1],
            "action": item[2],
            "actor": item[3],
            "old_value": item[4],
            "new_value": item[5],
            "created_at": item[6],
        }
        for item in audit_rows
    ]

    lei = None
    if lei_row:
        registered_on = lei_row[3]
        days_ago = (
            (date.today() - registered_on).days
            if registered_on else None
        )
        lei = {
            "lei_code": lei_row[0],
            "entity_status": lei_row[1],
            "registration_status": lei_row[2],
            "registered_on": registered_on,
            "days_ago": days_ago,
            "fresh": days_ago is not None and days_ago <= 90,
            "lou_code": lei_row[4],
            "gleif_url": lei_row[5],
            "last_seen": lei_row[6],
        }

    return templates.TemplateResponse(
        request=request,
        name="lead_detail.html",
        context={
            "lead": {
                "id": row[0],
                "company_name": row[1],
                "jurisdiction": row[2],
                "entity_type": _format_entity_type(row[3]),
                "incorporation_date": row[4],
                "registered_address": row[5],
                "source_system": row[6],
                "source_ref": row[7],
                "verify_url": row[8],
            },
            "score": score,
            "action": action,
            "audit_rows": audit_rendered,
            "lei": lei,
            "rm_names": RM_NAMES,
            "statuses": _STATUSES,
            "saved": False,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


@app.post("/leads/{lead_id}/action", response_class=HTMLResponse)
def lead_action(
    request: Request,
    lead_id: UUID,
    assigned_to: str = Form(""),
    status: str = Form("New"),
    notes: str = Form(""),
    contacted_at: str = Form(""),
    follow_up_at: str = Form(""),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT assigned_to, status, notes, contacted_at, follow_up_at FROM rm_actions WHERE company_id = %s",
                (lead_id,),
            )
            existing = cur.fetchone()
            old_value = None if existing is None else {
                "assigned_to": existing[0],
                "status": existing[1],
                "notes": existing[2],
                "contacted_at": existing[3].isoformat() if existing[3] else None,
                "follow_up_at": existing[4].isoformat() if existing[4] else None,
            }

            cur.execute(
                """
                INSERT INTO rm_actions (company_id, assigned_to, status, notes, contacted_at, follow_up_at)
                VALUES (%s, %s, %s, %s, NULLIF(%s, '')::date, NULLIF(%s, '')::date)
                ON CONFLICT (company_id) DO UPDATE SET
                    assigned_to = EXCLUDED.assigned_to,
                    status = EXCLUDED.status,
                    notes = EXCLUDED.notes,
                    contacted_at = EXCLUDED.contacted_at,
                    follow_up_at = EXCLUDED.follow_up_at,
                    updated_at = NOW()
                """,
                (lead_id, assigned_to or None, status, notes or None, contacted_at, follow_up_at),
            )

            new_value = {
                "assigned_to": assigned_to or None,
                "status": status,
                "notes": notes or None,
                "contacted_at": contacted_at or None,
                "follow_up_at": follow_up_at or None,
            }

            cur.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, actor, old_value, new_value, ip_address)
                VALUES ('company', %s, 'rm_action_updated', %s, %s, %s, NULL)
                """,
                (lead_id, (_read_actor(request) or "unknown"), Jsonb(old_value), Jsonb(new_value)),
            )
            conn.commit()

    return _render_action_panel(lead_id, assigned_to or "", status, notes, None if not contacted_at else datetime.strptime(contacted_at, "%Y-%m-%d"), None if not follow_up_at else datetime.strptime(follow_up_at, "%Y-%m-%d"), saved=True)


# --- Inline assign/status endpoint for queue HTMX ---
@app.post("/leads/{lead_id}/assign", response_class=HTMLResponse)
def lead_assign(
        request: Request,
        lead_id: UUID,
        assigned_to: str = Form(""),
        status: str = Form("New"),
):
        with get_conn() as conn:
                with conn.cursor() as cur:
                        cur.execute(
                                """
                                INSERT INTO rm_actions (company_id, assigned_to, status)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (company_id) DO UPDATE SET
                                        assigned_to = EXCLUDED.assigned_to,
                                        status = EXCLUDED.status,
                                        updated_at = NOW()
                                """,
                                (lead_id, assigned_to or None, status),
                        )
                        cur.execute(
                                """
                                INSERT INTO audit_log
                                    (entity_type, entity_id, action, actor, new_value)
                                VALUES ('company', %s, 'quick_assign', %s, %s)
                                """,
                                (lead_id, (_read_actor(request) or "unknown"),
                                 Jsonb({"assigned_to": assigned_to or None, "status": status})),
                        )
                        conn.commit()

                with conn.cursor() as cur:
                        cur.execute(
                                """
                                SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                                             c.incorporation_date, c.verify_url,
                                             qs.priority_score, qs.tier, qs.reason_summary,
                                             ra.assigned_to, ra.status, qs.refreshed_at
                                FROM queue_snapshot qs
                                JOIN companies c ON c.id = qs.canonical_company_id
                                LEFT JOIN rm_actions ra ON ra.company_id = c.id
                                WHERE c.id = %s
                                """,
                                (lead_id,),
                        )
                        row = cur.fetchone()

        if row is None:
                return HTMLResponse("<tr><td colspan='8'>Not found</td></tr>", status_code=404)

        r = {
                "id": row[0], "company_name": row[1], "jurisdiction": row[2],
                "entity_type": _format_entity_type(row[3]),
                "incorporation_date": row[4], "verify_url": row[5],
                "priority_score": row[6], "tier": row[7], "reason_summary": row[8],
                "assigned_to": row[9], "status": row[10],
        }

        score_pct = r["priority_score"]
        score_color = "#059669" if score_pct >= 70 else "#d97706" if score_pct >= 40 else "#d1d5db"
        assigned_opts = "".join(
                f'<option value="{nm}" {"selected" if nm == (r["assigned_to"] or "") else ""}>{nm}</option>'
                for nm in RM_NAMES
        )
        status_opts = "".join(
                f'<option value="{s}" {"selected" if s == (r["status"] or "New") else ""}>{s}</option>'
                for s in _STATUSES
        )
        verify = f'<a href="{r["verify_url"]}" target="_blank" style="font-size:11px">↗</a>' if r["verify_url"] else ""

        return HTMLResponse(f"""
        <tr>
            <td><a href="/leads/{r['id']}">{escape(r['company_name'])}</a></td>
            <td>{escape(r['jurisdiction'])}</td>
            <td>{escape(r['entity_type'])}</td>
            <td>{r['incorporation_date'] or '—'}</td>
            <td>
                <div style=\"min-width:44px\">
                    <div style=\"height:3px;border-radius:2px;margin-bottom:3px;width:{score_pct}%;background:{score_color}\"></div>
                    <strong>{score_pct}</strong>
                </div>
            </td>
            <td><span class=\"tier tier-{r['tier']}\">{r['tier']}</span></td>
            <td>
                <form hx-post=\"/leads/{r['id']}/assign\" hx-target=\"closest tr\" hx-swap=\"outerHTML\" style=\"margin:0\">
                    <input type=\"hidden\" name=\"status\" value=\"{escape(r['status'] or 'New')}\">
                    <select name=\"assigned_to\" onchange=\"this.form.requestSubmit()\"
                        style=\"font-size:11px;padding:2px 4px;border:1px solid #ddd;border-radius:4px;max-width:90px\">
                        <option value=\"\">—</option>
                        {assigned_opts}
                    </select>
                </form>
            </td>
            <td>
                <form hx-post=\"/leads/{r['id']}/assign\" hx-target=\"closest tr\" hx-swap=\"outerHTML\" style=\"margin:0\">
                    <input type=\"hidden\" name=\"assigned_to\" value=\"{escape(r['assigned_to'] or '')}\">
                    <select name=\"status\" onchange=\"this.form.requestSubmit()\"
                        style=\"font-size:11px;padding:2px 4px;border:1px solid #ddd;border-radius:4px;max-width:110px\">
                        {status_opts}
                    </select>
                </form>
            </td>
            <td>{verify}</td>
        </tr>""")


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"preview": None, "actor_names": ACTOR_NAMES, "current_actor": (_read_actor(request) or "")},
    )


@app.post("/upload", response_class=HTMLResponse)
def upload_preview(request: Request, file: UploadFile = File(...)):
    file_bytes = file.file.read()
    columns, rows, validation_errors = _parse_upload_csv(file_bytes)

    preview = {
        "id": None,
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
        "validation_errors": validation_errors,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_uploads (filename, uploaded_by, row_count, parsed_rows, validation_errors, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    file.filename or "upload.csv",
                    (_read_actor(request) or "unknown"),
                    len(rows),
                    Jsonb(rows),
                    Jsonb(validation_errors),
                    "pending" if not validation_errors else "rejected",
                ),
            )
            preview["id"] = cur.fetchone()[0]
            conn.commit()

    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"preview": preview, "actor_names": ACTOR_NAMES, "current_actor": (_read_actor(request) or "")},
    )


@app.post("/upload/{upload_id}/confirm")
def upload_confirm(upload_id: UUID):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Atomically claim the upload: only succeeds if status is currently 'pending'.
            # Concurrent requests will find no row to update and receive a 409.
            cur.execute(
                """
                UPDATE pending_uploads
                SET status = 'confirmed'
                WHERE id = %s AND status = 'pending'
                RETURNING parsed_rows, validation_errors
                """,
                (upload_id,),
            )
            claimed = cur.fetchone()
            if claimed is None:
                # Either not found or already confirmed/rejected
                cur.execute(
                    "SELECT 1 FROM pending_uploads WHERE id = %s",
                    (upload_id,),
                )
                exists = cur.fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="Upload not found")
                raise HTTPException(status_code=409, detail="Upload already confirmed or rejected")

            parsed_rows, validation_errors = claimed
            if validation_errors:
                # Roll back the status change — this upload has errors and cannot be confirmed
                conn.rollback()
                raise HTTPException(status_code=400, detail="Upload has validation errors and cannot be confirmed")

            parsed_rows = parsed_rows or []
            inserted = 0
            skipped_duplicates = 0
            for index, row in enumerate(parsed_rows, start=1):
                company_name = (row.get("company_name") or "").strip()
                jurisdiction = (row.get("jurisdiction") or "").strip()
                entity_type = (row.get("entity_type") or "").strip() or None
                website = (row.get("website") or "").strip() or None
                normalised_name = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", company_name.lower())).strip()
                source_ref = f"upload:{upload_id}:{index}"

                cur.execute(
                    """
                    SELECT id, source_system FROM companies
                    WHERE normalised_name = %s AND jurisdiction = %s
                    LIMIT 1
                    """,
                    (normalised_name, jurisdiction),
                )
                existing = cur.fetchone()
                if existing is not None:
                    skipped_duplicates += 1
                    logger.warning(
                        "upload_dedup_skipped",
                        extra={
                            "upload_id": str(upload_id),
                            "row_index": index,
                            "company_name": company_name,
                            "jurisdiction": jurisdiction,
                            "existing_company_id": str(existing[0]),
                            "existing_source_system": existing[1],
                        },
                    )
                    continue

                inserted += 1
                cur.execute(
                    """
                    INSERT INTO companies (
                        source_system, source_ref, company_name, normalised_name,
                        jurisdiction, entity_type, incorporation_date,
                        registered_address, sic_codes, website, verify_url, raw_data
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (source_system, source_ref) DO UPDATE SET
                        company_name = EXCLUDED.company_name,
                        normalised_name = EXCLUDED.normalised_name,
                        jurisdiction = EXCLUDED.jurisdiction,
                        entity_type = EXCLUDED.entity_type,
                        incorporation_date = EXCLUDED.incorporation_date,
                        registered_address = EXCLUDED.registered_address,
                        sic_codes = EXCLUDED.sic_codes,
                        website = EXCLUDED.website,
                        verify_url = EXCLUDED.verify_url,
                        raw_data = EXCLUDED.raw_data,
                        updated_at = NOW()
                    """,
                    (
                        "manual_upload",
                        source_ref,
                        company_name,
                        normalised_name,
                        jurisdiction,
                        entity_type,
                        None,
                        None,
                        [],
                        website,
                        None,
                        Jsonb(row),
                    ),
                )

            logger.info(
                "upload_confirmed",
                extra={
                    "upload_id": str(upload_id),
                    "rows_total": len(parsed_rows),
                    "rows_inserted": inserted,
                    "rows_skipped_duplicate": skipped_duplicates,
                },
            )
            conn.commit()

    return RedirectResponse(url="/", status_code=303)


_AUDIT_ACTION_LABELS = {
    "rm_action_updated": "Lead updated",
    "quick_assign": "Quick assign",
    "lead_status_changed": "Status changed",
    "lead_assigned": "Reassigned",
    "score_recalculated": "Score recalculated",
    "introducer_updated": "Introducer updated",
}

_AUDIT_FIELD_LABELS = {
    "assigned_to": "Assigned to",
    "status": "Status",
    "notes": "Notes",
    "contacted_at": "Contacted",
    "follow_up_at": "Follow-up",
    "tier": "Tier",
    "priority_score": "Score",
}


def _audit_action_label(action: str | None) -> str:
    if not action:
        return "—"
    return _AUDIT_ACTION_LABELS.get(action, action.replace("_", " ").capitalize())


def _audit_field_label(field: str) -> str:
    return _AUDIT_FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def _format_audit_value(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value
    return str(value)


def _audit_changes(old, new) -> list[dict]:
    """Return list of changed fields {label, old, new}. Falls back to a single
    raw 'new' row when payloads aren't dict-compatible."""
    if not isinstance(new, dict):
        if new is None and old is None:
            return []
        return [{"label": "Value", "old": _format_audit_value(old), "new": _format_audit_value(new)}]
    old_dict = old if isinstance(old, dict) else {}
    changes = []
    # union of keys preserves all transitions, including new keys
    for key in new.keys():
        old_v = old_dict.get(key)
        new_v = new.get(key)
        if old_v == new_v:
            continue
        changes.append({
            "label": _audit_field_label(key),
            "old": _format_audit_value(old_v),
            "new": _format_audit_value(new_v),
        })
    # if nothing changed but it's still a meaningful event, show a compact summary
    if not changes:
        for key, val in new.items():
            if val in (None, ""):
                continue
            changes.append({
                "label": _audit_field_label(key),
                "old": None,
                "new": _format_audit_value(val),
            })
    return changes


def _humanize_actor(actor: str | None) -> str:
    if not actor or actor.lower() in ("unknown", ""):
        return "Unknown"
    if actor.lower() == "system":
        return "System"
    return actor


def _relative_time(ts: datetime | None) -> str:
    if not ts:
        return ""
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 7 * 86400:
        return f"{secs // 86400}d ago"
    return ts.strftime("%d %b %Y")


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    filters = {
        "actor": request.query_params.get("actor", ""),
        "action": request.query_params.get("action", ""),
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
    }
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    page_size = 50

    where = ["1=1"]
    params: list[object] = []
    if filters["actor"]:
        where.append("a.actor = %s")
        params.append(filters["actor"])
    if filters["action"]:
        where.append("a.action = %s")
        params.append(filters["action"])
    if filters["date_from"]:
        where.append("a.created_at >= %s")
        params.append(filters["date_from"])
    if filters["date_to"]:
        where.append("a.created_at < (%s::date + INTERVAL '1 day')")
        params.append(filters["date_to"])

    where_sql = " AND ".join(where)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM audit_log a WHERE {where_sql}", params)
            total = cur.fetchone()[0]
            cur.execute(
                f"""
                SELECT a.entity_type, a.entity_id, a.action, a.actor,
                       a.old_value, a.new_value, a.created_at,
                       c.company_name
                FROM audit_log a
                LEFT JOIN companies c
                  ON a.entity_type = 'company' AND c.id = a.entity_id
                WHERE {where_sql}
                ORDER BY a.created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, (page - 1) * page_size],
            )
            rows = cur.fetchall()

            cur.execute("SELECT DISTINCT actor FROM audit_log WHERE actor IS NOT NULL ORDER BY 1")
            actor_options = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT action FROM audit_log WHERE action IS NOT NULL ORDER BY 1")
            action_options = [r[0] for r in cur.fetchall()]

    total_pages = max((total + page_size - 1) // page_size, 1)
    rendered_rows = []
    for row in rows:
        entity_type, entity_id, action, actor, old_v, new_v, created_at, company_name = row
        rendered_rows.append({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_url": f"/leads/{entity_id}" if entity_type == "company" else None,
            "entity_label": company_name or (str(entity_id)[:8] if entity_id else "—"),
            "action": action,
            "action_label": _audit_action_label(action),
            "actor": _humanize_actor(actor),
            "actor_raw": actor or "",
            "changes": _audit_changes(old_v, new_v),
            "created_at": created_at,
            "created_at_iso": created_at.isoformat() if created_at else "",
            "created_at_display": created_at.strftime("%d %b %Y %H:%M") if created_at else "",
            "created_at_relative": _relative_time(created_at),
        })

    action_choices = [(a, _audit_action_label(a)) for a in action_options]

    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "rows": rendered_rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
            "filters": filters,
            "actor_options": actor_options,
            "action_choices": action_choices,
        },
    )

