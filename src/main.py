import base64
import csv
import hmac
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

from src.contact_discovery import (
    acceptance_target,
    prepare_contact_acceptance,
    review_status,
    suggestion_category,
)
from src.config import (
    ACTOR_NAMES,
    ADMIN_TOKEN,
    APP_ENV,
    BASIC_AUTH_PASS,
    BASIC_AUTH_USER,
    CH_ENRICHMENT_SAFE_LIMIT,
    LOG_LEVEL,
    RM_NAMES,
    SECRET_KEY,
)
from src.db import check_connection, get_conn
from src.ingestion.companies_house import run_ch_enrichment_batch
from src.ingestion.lei_backfill import backfill_lei_company_links
from src.route_intelligence import (
    CONTACTABILITY_LABELS,
    build_route_recommendation,
)
from src.scoring import (
    SCORING_VERSION,
    SIGNAL_DETAILS,
    contact_path_label,
    derive_next_action,
    derive_queue_next_action,
    introducer_route_hint,
    suggested_contact_route,
)

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

_CANDIDATE_SUGGESTION_SQL = r"""
    cds.suggestion_type NOT IN ('registry', 'regulator')
    AND NOT (cds.suggested_value ~* '^https?://(www\.)?google\.com/search')
    AND (
        (cds.suggestion_type = 'generic_email'
         AND cds.suggested_value ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
        OR (cds.suggestion_type IN ('website', 'contact_page')
            AND cds.suggested_value ~* '^https?://')
        OR (cds.suggestion_type = 'company_linkedin'
            AND cds.suggested_value ~* '^https?://([^/]+\.)?linkedin\.com/(company|showcase)/')
        OR (cds.suggestion_type IN ('csp_route', 'introducer_route')
            AND TRIM(cds.suggested_value) <> '')
    )
"""


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


def _require_admin_token(request: Request) -> None:
    """Enforce bearer-token auth on admin routes. Constant-time compare;
    never logs the token value."""
    if not ADMIN_TOKEN:
        logger.error("admin_token_not_configured")
        raise HTTPException(status_code=503, detail="Admin not configured")
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not hmac.compare_digest(presented, ADMIN_TOKEN):
        logger.warning("admin_token_mismatch")
        raise HTTPException(status_code=401, detail="Authentication required")


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


def build_contact_research_links(
    *,
    company_name: str,
    jurisdiction: str,
    source_ref: str | None,
    registered_address: str | None,
    verify_url: str | None,
    officers: list[dict],
) -> dict[str, list[dict[str, str]]]:
    """Build deterministic research shortcuts without fetching external data."""

    def search(label: str, query: str) -> dict[str, str]:
        return {
            "label": label,
            "url": f"https://www.google.com/search?{urlencode({'q': query})}",
        }

    company = [
        search("Official website", f'"{company_name}" {jurisdiction} official website'),
        search("Contact page", f'"{company_name}" contact'),
        search("Company LinkedIn", f'"{company_name}" LinkedIn company'),
    ]
    people = [
        search(
            f"{officer['name']} on LinkedIn",
            f'"{officer["name"]}" "{company_name}" LinkedIn',
        )
        for officer in officers
        if officer.get("name") and not officer.get("resigned_on")
    ][:5]
    introducer = [
        search(
            "Introducer / CSP route",
            f'"{company_name}" management company OR fiduciary OR corporate services',
        )
    ]
    if registered_address:
        introducer.insert(
            0,
            search(
                "Registered office route",
                f'"{registered_address}" "{company_name}" '
                "management company OR corporate service provider",
            ),
        )

    registry: list[dict[str, str]] = []
    if verify_url:
        registry.append({"label": "Open official registry", "url": verify_url})
    identity = f'"{company_name}" {source_ref or ""}'.strip()
    if jurisdiction == "UK":
        registry.append(search("FCA context", f"{identity} site:register.fca.org.uk"))
    elif jurisdiction == "Mauritius":
        registry.append(search("FSC context", f"{identity} site:fscmauritius.org"))
    else:
        registry.append(
            search("Registry / regulator context", f"{identity} registry regulator")
        )

    return {
        "company": company,
        "people": people,
        "introducer": introducer,
        "registry": registry,
    }


app = FastAPI(
    title="Arie Leads",
    docs_url=None if APP_ENV == "production" else "/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")

from src.introducers import router as introducers_router  # noqa: E402

app.include_router(introducers_router)


def _auth_exempt(path: str) -> bool:
    """Paths that must stay open: health/liveness probes, static assets, and
    machine endpoints that carry their own auth (admin bearer, internal secret)."""
    return (
        path in ("/live", "/health", "/openapi.json")
        or path.startswith("/static")
        or path.startswith("/admin")
        or path.startswith("/internal")
        or path.startswith("/docs")
    )


@app.middleware("http")
async def _basic_auth_middleware(request: Request, call_next):
    """Minimal pilot access protection. Active only when BASIC_AUTH_USER and
    BASIC_AUTH_PASS are both configured (read live so tests can toggle it).
    Not a substitute for SSO/RBAC — sized for a 2-user internal pilot."""
    user = BASIC_AUTH_USER
    pw = BASIC_AUTH_PASS
    if user and pw and not _auth_exempt(request.url.path):
        ok = False
        header = request.headers.get("authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() == "basic" and encoded:
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
                presented_user, _, presented_pw = decoded.partition(":")
                ok = hmac.compare_digest(presented_user, user) and hmac.compare_digest(
                    presented_pw, pw
                )
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                content="Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="Arie Leads"'},
            )
    return await call_next(request)

_STATUSES = [
    "New",
    "Researching",
    "Qualified",
    "Outreach Ready",
    "Contacted",
    "Opportunity",
    "Client",
    "Closed — Not Fit",
]


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


def _render_action_panel(
    lead_id: UUID,
    assigned_to: str,
    status: str,
    notes: str,
    contacted_at,
    follow_up_at,
    saved: bool = False,
) -> HTMLResponse:
    return HTMLResponse(f"""
        <div class="card" id="action-panel">
          <h2>RM Progress</h2>
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
        """)


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
                    cur.execute(
                        "SELECT COUNT(*), MAX(refreshed_at) FROM queue_snapshot"
                    )
                    row = cur.fetchone()
                    if row:
                        queue_rows = row[0] or 0
                        queue_refreshed_at = row[1]
                        if queue_refreshed_at:
                            if queue_refreshed_at.tzinfo is None:
                                queue_refreshed_at = queue_refreshed_at.replace(
                                    tzinfo=timezone.utc
                                )
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
                    cur.execute("""
                        SELECT started_at, completed_at, status, uk_count, mu_count,
                               scores_count, queue_rows, duration_seconds, error
                        FROM pipeline_runs
                        ORDER BY started_at DESC
                        LIMIT 1
                        """)
                    pr_row = cur.fetchone()
                    if pr_row:
                        started_at = pr_row[0]
                        completed_at = pr_row[1]
                        if started_at and started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=timezone.utc)
                        if completed_at and completed_at.tzinfo is None:
                            completed_at = completed_at.replace(tzinfo=timezone.utc)
                        last_pipeline_run = {
                            "started_at": (
                                started_at.isoformat() if started_at else None
                            ),
                            "completed_at": (
                                completed_at.isoformat() if completed_at else None
                            ),
                            "status": pr_row[2],
                            "uk_count": pr_row[3],
                            "mu_count": pr_row[4],
                            "scores_count": pr_row[5],
                            "queue_rows": pr_row[6],
                            "duration_seconds": (
                                float(pr_row[7]) if pr_row[7] is not None else None
                            ),
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
        "queue_refreshed_at": (
            queue_refreshed_at.isoformat() if queue_refreshed_at else None
        ),
        "queue_fresh": queue_fresh,
        "mauritius_last_seen": (
            mauritius_last_seen.isoformat() if mauritius_last_seen else None
        ),
        "last_pipeline_run": last_pipeline_run,
        "scoring_version": SCORING_VERSION,
    }


@app.get("/live")
def live(response: Response):
    db_ok = check_connection()
    if not db_ok:
        response.status_code = 503
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "connected" if db_ok else "unreachable",
        "scoring_version": SCORING_VERSION,
    }


@app.post("/admin/lei-backfill")
def admin_lei_backfill(request: Request):
    _require_admin_token(request)
    with get_conn() as conn:
        result = backfill_lei_company_links(conn)
    return result


@app.post("/admin/ch-enrichment")
def admin_ch_enrichment(request: Request, limit: int = None):
    _require_admin_token(request)
    actual_limit = limit if limit is not None else CH_ENRICHMENT_SAFE_LIMIT
    with get_conn() as conn:
        result = run_ch_enrichment_batch(conn, actual_limit)
    return result


@app.post("/me")
def set_actor(request: Request, actor: str = Form("")):
    raw_referer = request.headers.get("referer", "")
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw_referer)
        same_origin = parsed.scheme in (
            "http",
            "https",
        ) and parsed.netloc == request.headers.get("host", "")
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
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE qs.tier = 'HIGH'
                          AND COALESCE(ra.status, 'New') IN ('New', 'Researching')
                    ) AS prospects_for_action,
                    COUNT(*) FILTER (
                        WHERE qs.tier = 'HIGH'
                          AND COALESCE(ls.reachability_status, 'research_required') = 'no_contact_path'
                    ) AS high_fit_contact_missing,
                    COUNT(*) FILTER (
                        WHERE COALESCE(ls.reachability_status, 'research_required') = 'ready_outreach'
                          AND COALESCE(ra.status, 'New') NOT IN ('Client', 'Closed — Not Fit')
                    ) AS ready_to_contact,
                    COUNT(*) FILTER (
                        WHERE CASE
                            WHEN %s <> '' THEN ra.assigned_to = %s
                            ELSE ra.assigned_to IS NOT NULL
                        END
                    ) AS assigned_leads,
                    COUNT(*) FILTER (
                        WHERE ra.assigned_to IS NOT NULL
                          AND COALESCE(ra.updated_at, c.updated_at) < NOW() - INTERVAL '14 days'
                          AND COALESCE(ra.status, 'New') NOT IN ('Client', 'Closed — Not Fit')
                    ) AS stale_assigned,
                    COUNT(*) FILTER (
                        WHERE c.jurisdiction = 'Mauritius'
                          AND qs.tier IN ('HIGH', 'MEDIUM')
                    ) AS introducer_routes,
                    COUNT(*) FILTER (
                        WHERE ra.status IN ('Opportunity', 'Client')
                          AND ra.updated_at >= NOW() - INTERVAL '30 days'
                    ) AS recent_progression
                FROM queue_snapshot qs
                JOIN companies c ON c.id = qs.canonical_company_id
                JOIN lead_scores ls
                  ON ls.company_id = qs.canonical_company_id
                 AND ls.is_current = TRUE
                LEFT JOIN rm_actions ra ON ra.company_id = c.id
                """,
                (actor, actor),
            )
            action_metrics_row = cur.fetchone()

            cur.execute(f"""
                WITH latest_routes AS (
                    SELECT DISTINCT ON (lead_id)
                           lead_id, contactability_bucket, status
                    FROM route_recommendations
                    WHERE status <> 'superseded'
                    ORDER BY lead_id, generated_at DESC
                )
                SELECT
                    COUNT(*) FILTER (WHERE contactability_bucket = 'ready_to_contact'),
                    COUNT(*) FILTER (
                        WHERE contactability_bucket = 'route_via_introducer_csp'
                    ),
                    COUNT(*) FILTER (
                        WHERE contactability_bucket = 'direct_candidate_found'
                    ),
                    COUNT(*) FILTER (WHERE contactability_bucket IN (
                        'management_company_route_likely', 'registry_evidence_only',
                        'needs_route_research'
                    )),
                    COUNT(*) FILTER (WHERE contactability_bucket = 'no_usable_route'),
                    (
                        SELECT COUNT(*) FROM contact_discovery_suggestions cds
                        WHERE cds.status = 'Needs Review'
                          AND ({_CANDIDATE_SUGGESTION_SQL})
                    ),
                    COUNT(*) FILTER (WHERE status = 'accepted'),
                    COUNT(*) FILTER (WHERE status = 'rejected')
                FROM latest_routes
            """)
            route_metrics_row = cur.fetchone()

            cur.execute(
                "SELECT status, COUNT(*) AS cnt FROM rm_actions GROUP BY status ORDER BY cnt DESC"
            )
            status_counts = cur.fetchall()

            # introducer_actions is UNIQUE(introducer_id), so a per-introducer
            # action COUNT is always 0/1 and meaningless. Surface operational
            # metrics instead: total, contactable, qualified, recently actioned.
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM introducers) AS total,
                    (SELECT COUNT(*) FROM introducers
                       WHERE TRIM(COALESCE(contact_email, '')) <> ''
                          OR TRIM(COALESCE(phone_number, '')) <> ''
                          OR TRIM(COALESCE(contact_name, '')) <> '') AS with_contact,
                    (SELECT COUNT(*) FROM introducer_actions WHERE status = 'Qualified') AS qualified,
                    (SELECT COUNT(*) FROM introducer_actions
                       WHERE updated_at >= NOW() - INTERVAL '30 days') AS recent
            """)
            intro_stats_row = cur.fetchone()

            cur.execute("""
                SELECT i.company_name, i.category, ia.status, ia.assigned_to, ia.updated_at
                FROM introducers i
                LEFT JOIN introducer_actions ia ON ia.introducer_id = i.id
                ORDER BY ia.updated_at DESC NULLS LAST, i.company_name ASC
                LIMIT 5
            """)
            recent_introducers = cur.fetchall()

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
                (
                    f"UK {last_success_row[1] or 0} · "
                    f"MU {last_success_row[2] or 0} · "
                    f"Scores {last_success_row[3] or 0}"
                )
                if last_success_row
                else "—"
            ),
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

    total_uk = cov_row[0] if cov_row else 0
    enriched_uk = cov_row[1] if cov_row else 0
    with_officers = cov_row[2] if cov_row else 0
    with_pscs = cov_row[3] if cov_row else 0
    total_lei = cov_row[4] if cov_row else 0
    linked_lei = cov_row[5] if cov_row else 0
    total_mu = cov_row[6] if cov_row else 0

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "actor": actor,
            # Panel 1
            "sources": sources,
            # Panel 2
            "total_leads": vol_row[0] if vol_row else 0,
            "leads_7d": vol_row[1] if vol_row else 0,
            "leads_30d": vol_row[2] if vol_row else 0,
            "s0_39": score_row[0] if score_row else 0,
            "s40_59": score_row[1] if score_row else 0,
            "s60_79": score_row[2] if score_row else 0,
            "s80_100": score_row[3] if score_row else 0,
            "total_scored": score_row[4] if score_row else 0,
            # Panel 3
            "total_uk": total_uk,
            "pct_enriched_uk": _pct(enriched_uk, total_uk),
            "with_officers": with_officers,
            "with_pscs": with_pscs,
            "total_lei": total_lei,
            "pct_lei_linked": _pct(linked_lei, total_lei),
            "total_mu": total_mu,
            # Panel 4
            "action_metrics": {
                "prospects_for_action": action_metrics_row[0] if action_metrics_row else 0,
                "high_fit_contact_missing": action_metrics_row[1] if action_metrics_row else 0,
                "ready_to_contact": action_metrics_row[2] if action_metrics_row else 0,
                "assigned_leads": action_metrics_row[3] if action_metrics_row else 0,
                "stale_assigned": action_metrics_row[4] if action_metrics_row else 0,
                "introducer_routes": action_metrics_row[5] if action_metrics_row else 0,
                "recent_progression": action_metrics_row[6] if action_metrics_row else 0,
                "assigned_label": "Assigned to me" if actor else "Assigned leads",
            },
            "status_counts": status_counts,
            "route_metrics": {
                "ready_to_contact": route_metrics_row[0] if route_metrics_row else 0,
                "via_introducer_csp": route_metrics_row[1] if route_metrics_row else 0,
                "direct_candidate": route_metrics_row[2] if route_metrics_row else 0,
                "needs_research": route_metrics_row[3] if route_metrics_row else 0,
                "no_usable_route": route_metrics_row[4] if route_metrics_row else 0,
                "suggestions_awaiting_review": (
                    route_metrics_row[5] if route_metrics_row else 0
                ),
                "accepted_routes": route_metrics_row[6] if route_metrics_row else 0,
                "rejected_routes": route_metrics_row[7] if route_metrics_row else 0,
            },
            "introducer_stats": {
                "total": intro_stats_row[0] if intro_stats_row else 0,
                "with_contact": intro_stats_row[1] if intro_stats_row else 0,
                "qualified": intro_stats_row[2] if intro_stats_row else 0,
                "recent": intro_stats_row[3] if intro_stats_row else 0,
            },
            "recent_introducers": [
                {
                    "company_name": r[0],
                    "category": r[1],
                    "status": r[2],
                    "assigned_to": r[3],
                    "updated_at": r[4],
                }
                for r in recent_introducers
            ],
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


@app.get("/", response_class=HTMLResponse)
def queue(request: Request):
    filters = {
        "tier": request.query_params.get("tier", ""),
        "jurisdiction": request.query_params.get("jurisdiction", ""),
        "assigned_to": request.query_params.get("assigned_to", ""),
        "status": request.query_params.get("status", ""),
        "contact_readiness": request.query_params.get("contact_readiness", ""),
        "contact_suggestions": request.query_params.get("contact_suggestions", ""),
        "route_bucket": request.query_params.get("route_bucket", ""),
        "named_route": request.query_params.get("named_route", ""),
        "introducer_match": request.query_params.get("introducer_match", ""),
        "office_cluster": request.query_params.get("office_cluster", ""),
        "view": request.query_params.get("view", ""),
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "sort": request.query_params.get("sort", "score"),
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
    if filters["contact_readiness"] in {
        "ready_outreach",
        "research_required",
        "no_contact_path",
    }:
        where_clauses.append(
            "COALESCE(ls.reachability_status, 'research_required') = %s"
        )
        params.append(filters["contact_readiness"])
    if filters["contact_suggestions"] == "has":
        where_clauses.append(
            "EXISTS (SELECT 1 FROM contact_discovery_suggestions cds "
            f"WHERE cds.company_id = c.id AND ({_CANDIDATE_SUGGESTION_SQL}))"
        )
    elif filters["contact_suggestions"] == "needs_review":
        where_clauses.append(
            "EXISTS (SELECT 1 FROM contact_discovery_suggestions cds "
            "WHERE cds.company_id = c.id AND cds.status = 'Needs Review' "
            f"AND ({_CANDIDATE_SUGGESTION_SQL}))"
        )
    if filters["route_bucket"] in CONTACTABILITY_LABELS:
        where_clauses.append(
            """(
                SELECT rr.contactability_bucket FROM route_recommendations rr
                WHERE rr.lead_id = c.id AND rr.status <> 'superseded'
                ORDER BY rr.generated_at DESC LIMIT 1
            ) = %s"""
        )
        params.append(filters["route_bucket"])
    if filters["named_route"] == "yes":
        where_clauses.append(
            """NULLIF(TRIM((
                SELECT rr.best_route_value FROM route_recommendations rr
                WHERE rr.lead_id = c.id AND rr.status <> 'superseded'
                ORDER BY rr.generated_at DESC LIMIT 1
            )), '') IS NOT NULL"""
        )
    if filters["introducer_match"] == "yes":
        where_clauses.append(
            """EXISTS (
                SELECT 1 FROM introducer_matches im
                WHERE im.lead_id = c.id AND im.status IN ('pending', 'accepted')
            )"""
        )
    if filters["office_cluster"] == "yes":
        where_clauses.append(
            """NULLIF(TRIM(c.registered_address), '') IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM companies clustered
                WHERE clustered.id <> c.id
                  AND NULLIF(TRIM(clustered.registered_address), '') IS NOT NULL
                  AND regexp_replace(lower(clustered.registered_address), '[^a-z0-9]+', '', 'g')
                    = regexp_replace(lower(c.registered_address), '[^a-z0-9]+', '', 'g')
            )"""
        )
    if filters["view"] == "best":
        where_clauses.extend(
            [
                "qs.tier = 'HIGH'",
                "COALESCE(ra.status, 'New') IN ('New', 'Researching')",
            ]
        )
    elif filters["view"] == "ready":
        where_clauses.append(
            "COALESCE(ls.reachability_status, 'research_required') = 'ready_outreach'"
        )
    elif filters["view"] == "high_no_contact":
        where_clauses.extend(
            [
                "qs.tier = 'HIGH'",
                "COALESCE(ls.reachability_status, 'research_required') = 'no_contact_path'",
            ]
        )
    elif filters["view"] == "mine" and _read_actor(request):
        where_clauses.append("ra.assigned_to = %s")
        params.append(_read_actor(request))
    elif filters["view"] == "assigned":
        where_clauses.append("ra.assigned_to IS NOT NULL")
    elif filters["view"] == "unassigned_high":
        where_clauses.extend(["qs.tier = 'HIGH'", "ra.assigned_to IS NULL"])
    elif filters["view"] == "stale":
        where_clauses.extend(
            [
                "ra.assigned_to IS NOT NULL",
                "COALESCE(ra.updated_at, c.updated_at) < NOW() - INTERVAL '14 days'",
                "COALESCE(ra.status, 'New') NOT IN ('Client', 'Closed — Not Fit')",
            ]
        )
    elif filters["view"] == "introducer":
        where_clauses.extend(
            ["c.jurisdiction = 'Mauritius'", "qs.tier IN ('HIGH', 'MEDIUM')"]
        )
    elif filters["view"] == "progressed":
        where_clauses.extend(
            [
                "ra.status IN ('Onboarding', 'Opportunity')",
                "ra.updated_at >= NOW() - INTERVAL '30 days'",
            ]
        )
    elif filters["view"] == "suggestions":
        where_clauses.append(
            "EXISTS (SELECT 1 FROM contact_discovery_suggestions cds "
            f"WHERE cds.company_id = c.id AND ({_CANDIDATE_SUGGESTION_SQL}))"
        )
    elif filters["view"] == "suggestion_review":
        where_clauses.append(
            "EXISTS (SELECT 1 FROM contact_discovery_suggestions cds "
            "WHERE cds.company_id = c.id AND cds.status = 'Needs Review' "
            f"AND ({_CANDIDATE_SUGGESTION_SQL}))"
        )
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
    }.get(
        filters["sort"],
        "c.incorporation_date DESC NULLS LAST, c.jurisdiction ASC, qs.priority_score DESC, c.company_name ASC",
    )

    count_sql = f"""
        SELECT COUNT(*)
        FROM queue_snapshot qs
        JOIN companies c ON c.id = qs.canonical_company_id
        LEFT JOIN rm_actions ra ON ra.company_id = c.id
        LEFT JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
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
            qs.refreshed_at,
            COALESCE(ls.reachability_status, 'research_required'),
            (SELECT COUNT(*) FROM contact_discovery_suggestions cds
             WHERE cds.company_id = c.id AND ({_CANDIDATE_SUGGESTION_SQL})),
            (SELECT COUNT(*) FROM contact_discovery_suggestions cds
             WHERE cds.company_id = c.id AND cds.status = 'Needs Review'
               AND ({_CANDIDATE_SUGGESTION_SQL})),
            rr.contactability_bucket,
            rr.best_route_type,
            rr.best_route_value,
            rr.confidence,
            rr.next_action,
            rr.status,
            rr.generated_at,
            rr.route_candidate_id
        FROM queue_snapshot qs
        JOIN companies c ON c.id = qs.canonical_company_id
        LEFT JOIN rm_actions ra ON ra.company_id = c.id
        LEFT JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
        LEFT JOIN LATERAL (
            SELECT contactability_bucket, best_route_type, best_route_value,
                   confidence, next_action, status, generated_at, route_candidate_id
            FROM route_recommendations
            WHERE lead_id = c.id AND status <> 'superseded'
            ORDER BY generated_at DESC
            LIMIT 1
        ) rr ON TRUE
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
            "contact_readiness": row[12],
            "contact_readiness_label": contact_path_label(row[12]),
            "candidate_route_count": row[13] if len(row) > 13 else 0,
            "candidates_needing_review": row[14] if len(row) > 14 else 0,
            "route_bucket": row[15] if len(row) > 15 and row[15] else "needs_route_research",
            "contactability_label": CONTACTABILITY_LABELS.get(
                row[15] if len(row) > 15 and row[15] else "needs_route_research",
                "Needs Route Research",
            ),
            "best_route": (
                row[16].replace("_", " ").title()
                if len(row) > 16 and row[16]
                else "Not evaluated"
            ),
            "route_candidate": row[17] if len(row) > 17 else None,
            "route_confidence": (
                row[18].title() if len(row) > 18 and row[18] else "Not evaluated"
            ),
            "next_action": (
                row[19]
                if len(row) > 19 and row[19]
                else derive_queue_next_action(
                    assigned_to=row[9],
                    rm_status=row[10],
                    reachability_status=row[12],
                    jurisdiction=row[2],
                    entity_type=row[3],
                )
            ),
            "route_status": row[20] if len(row) > 20 else None,
            "route_generated_at": row[21] if len(row) > 21 else None,
            "route_candidate_id": row[22] if len(row) > 22 else None,
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
            "refreshed_at": (
                f"{refreshed_at.day} {refreshed_at.strftime('%b %Y, %H:%M')} UTC"
                if refreshed_at
                else None
            ),
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
                      c.source_ref, c.verify_url, c.website,
                      ls.score, ls.tier, ls.reason_codes, ls.reason_summary, ls.scoring_version,
                      ra.assigned_to, ra.status, ra.notes, ra.contacted_at, ra.follow_up_at,
                      ls.arie_fit_score, ls.priority_score, ls.reachability_status,
                      ls.lead_readiness, ls.enrichment_tier, ls.why_reasons,
                      ls.freshness_score, ls.keyword_score,
                      c.updated_at, c.last_enriched_at
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

            cur.execute(
                """
                SELECT lei_code, legal_name, registered_as, entity_status,
                       registration_status, registered_on, managing_lou,
                       gleif_url, last_seen
                FROM lei_records
                WHERE company_id IS NULL
                  AND (
                    registered_as = %s
                    OR regexp_replace(lower(legal_name), '[^a-z0-9]+', '', 'g')
                       = regexp_replace(lower(%s), '[^a-z0-9]+', '', 'g')
                  )
                ORDER BY last_seen DESC NULLS LAST, registered_on DESC NULLS LAST
                LIMIT 5
                """,
                (row[7], row[1]),
            )
            lei_reverse_rows = cur.fetchall()

            cur.execute(
                """
                  SELECT id, officer_name, role, appointed_on, resigned_on,
                      nationality, country_of_residence, email
                FROM company_officers
                WHERE company_id = %s
                ORDER BY appointed_on DESC NULLS LAST, officer_name
                """,
                (lead_id,),
            )
            officers_rows = cur.fetchall()

            cur.execute(
                """
                  SELECT id, name, kind, nationality, country_of_residence,
                      natures_of_control, notified_on, ceased_on, email
                FROM company_pscs
                WHERE company_id = %s
                ORDER BY notified_on DESC NULLS LAST, name
                """,
                (lead_id,),
            )
            pscs_rows = cur.fetchall()

            cur.execute(
                """
                SELECT website, generic_email, contact_form_url, linkedin_url,
                       source, confidence, verified_at, checked_by
                FROM company_contacts
                WHERE company_id = %s
                """,
                (lead_id,),
            )
            company_contact_row = cur.fetchone()

            cur.execute(
                """
                SELECT id, suggestion_type, suggested_value, source_name,
                       source_url, search_query, confidence, confidence_reason,
                       status, discovered_at, reviewed_by, reviewed_at, notes
                FROM contact_discovery_suggestions
                WHERE company_id = %s
                ORDER BY
                    CASE status WHEN 'Needs Review' THEN 0 WHEN 'Accepted' THEN 1 ELSE 2 END,
                    discovered_at DESC
                """,
                (lead_id,),
            )
            suggestion_rows = cur.fetchall()

            cur.execute(
                """
                SELECT id, contactability_bucket, best_route_type,
                       best_route_value, route_candidate_id, rationale,
                       evidence_summary, missing_data, next_action, confidence,
                       generated_by, generated_at, reviewed_by, reviewed_at, status
                FROM route_recommendations
                WHERE lead_id = %s AND status <> 'superseded'
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (lead_id,),
            )
            route_recommendation_row = cur.fetchone()

            cur.execute(
                """
                SELECT im.id, im.introducer_id, i.company_name, i.category,
                       im.match_type, im.match_strength, im.evidence, im.source,
                       im.status, im.created_at, im.reviewed_by, im.reviewed_at
                FROM introducer_matches im
                JOIN introducers i ON i.id = im.introducer_id
                WHERE im.lead_id = %s AND im.status IN ('pending', 'accepted')
                ORDER BY
                    CASE im.match_strength WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    im.created_at DESC
                """,
                (lead_id,),
            )
            introducer_match_rows = cur.fetchall()

    score = {
        "score": row[10] if row[10] is not None else 0,
        "tier": row[11] if row[11] is not None else "LOW",
        "reason_codes": row[12] if row[12] is not None else [],
        "reason_summary": row[13] if row[13] is not None else "No signals matched.",
        "scoring_version": row[14] if row[14] is not None else SCORING_VERSION,
        "arie_fit_score": row[20] if row[20] is not None else 0,
        "priority_score": row[21] if row[21] is not None else 0,
        "reachability_status": row[22] or "research_required",
        "lead_readiness": row[23] or "discovered",
        "enrichment_tier": row[24] or "C",
        "why_reasons": row[25] if row[25] is not None else [],
        "freshness_score": row[26] if row[26] is not None else 0,
        "keyword_score": row[27] if row[27] is not None else 0,
    }
    score["priority_pending"] = (
        score["scoring_version"] != SCORING_VERSION
        and score["priority_score"] == 0
        and score["arie_fit_score"] == 0
    )
    action = {
        "assigned_to": row[15],
        "status": row[16] or "New",
        "notes": row[17] or "",
        "contacted_at": row[18],
        "follow_up_at": row[19],
    }
    route_hint = introducer_route_hint(row[2], row[3])
    next_action = derive_next_action(
        rm_status=action["status"],
        reachability_status=score["reachability_status"],
        has_introducer=bool(route_hint)
        and score["reachability_status"] != "ready_outreach",
    )
    contact_path = contact_path_label(score["reachability_status"])
    suggested_route = suggested_contact_route(
        jurisdiction=row[2],
        entity_type=row[3],
        reachability_status=score["reachability_status"],
        has_officers=bool(officers_rows),
        has_pscs=bool(pscs_rows),
    )
    confidence_value = (
        float(company_contact_row[5])
        if company_contact_row and company_contact_row[5] is not None
        else None
    )
    company_contact = {
        "website": company_contact_row[0] if company_contact_row else row[9],
        "generic_email": company_contact_row[1] if company_contact_row else None,
        "contact_form_url": company_contact_row[2] if company_contact_row else None,
        "linkedin_url": company_contact_row[3] if company_contact_row else None,
        "source": company_contact_row[4] if company_contact_row else None,
        "confidence": (
            "High"
            if confidence_value is not None and confidence_value >= 0.8
            else "Medium"
            if confidence_value is not None and confidence_value >= 0.5
            else "Low"
            if confidence_value is not None
            else ""
        ),
        "last_checked": company_contact_row[6] if company_contact_row else None,
        "checked_by": company_contact_row[7] if company_contact_row else None,
    }
    suggestion_labels = {
        "website": "Website",
        "contact_page": "Contact page",
        "generic_email": "Generic email",
        "company_linkedin": "Company LinkedIn",
        "registry": "Registry",
        "regulator": "Regulator",
        "csp_route": "CSP route",
        "introducer_route": "Introducer route",
    }
    contact_suggestions = [
        {
            "id": item[0],
            "type": item[1],
            "type_label": suggestion_labels.get(item[1], item[1].replace("_", " ").title()),
            "value": item[2],
            "source_name": item[3],
            "source_url": item[4],
            "search_query": item[5],
            "confidence": item[6],
            "confidence_reason": item[7],
            "status": item[8],
            "discovered_at": item[9],
            "reviewed_by": item[10],
            "reviewed_at": item[11],
            "notes": item[12],
            "can_populate_contact": acceptance_target(item[1], item[2]) is not None,
            "category": suggestion_category(item[1], item[2]),
        }
        for item in suggestion_rows
    ]
    candidate_contact_routes = [
        item for item in contact_suggestions if item["category"] == "candidate"
    ]
    verification_sources = [
        item for item in contact_suggestions if item["category"] == "verification"
    ]
    introducer_matches = [
        {
            "id": item[0],
            "introducer_id": item[1],
            "introducer": {
                "id": item[1],
                "company_name": item[2],
                "category": item[3],
            },
            "company_name": item[2],
            "category": item[3],
            "match_type": item[4],
            "match_strength": item[5],
            "evidence": item[6],
            "source": item[7],
            "status": item[8],
            "created_at": item[9],
            "reviewed_by": item[10],
            "reviewed_at": item[11],
        }
        for item in introducer_match_rows
    ]
    if route_recommendation_row:
        route_intelligence = {
            "id": route_recommendation_row[0],
            "contactability_bucket": route_recommendation_row[1],
            "contactability_label": CONTACTABILITY_LABELS.get(
                route_recommendation_row[1], "Needs Route Research"
            ),
            "best_route_type": route_recommendation_row[2],
            "best_route_label": route_recommendation_row[2]
            .replace("_", " ")
            .title(),
            "best_route_value": route_recommendation_row[3],
            "route_candidate_id": route_recommendation_row[4],
            "rationale": route_recommendation_row[5],
            "evidence_summary": route_recommendation_row[6] or [],
            "missing_data": route_recommendation_row[7] or [],
            "next_action": route_recommendation_row[8],
            "confidence": route_recommendation_row[9],
            "generated_by": route_recommendation_row[10],
            "generated_at": route_recommendation_row[11],
            "reviewed_by": route_recommendation_row[12],
            "reviewed_at": route_recommendation_row[13],
            "status": route_recommendation_row[14],
            "persisted": True,
        }
    else:
        route_intelligence = build_route_recommendation(
            lead={
                "company_id": str(row[0]),
                "company_name": row[1],
                "jurisdiction": row[2],
                "entity_type": row[3],
                "registered_address": row[5],
                "verify_url": row[8],
                "website": company_contact["website"],
                "generic_email": company_contact["generic_email"],
                "contact_form_url": company_contact["contact_form_url"],
                "linkedin_url": company_contact["linkedin_url"],
                "contact_confidence": company_contact["confidence"],
            },
            introducer_matches=introducer_matches,
        )
        route_intelligence.update(
            {
                "id": None,
                "best_route_label": str(route_intelligence["best_route_type"])
                .replace("_", " ")
                .title(),
                "generated_at": company_contact["last_checked"],
                "persisted": False,
            }
        )
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
    last_activity = audit_rendered[0]["created_at"] if audit_rendered else None

    lei = None
    if lei_row:
        registered_on = lei_row[3]
        days_ago = (date.today() - registered_on).days if registered_on else None
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

    officers = [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "appointed_on": r[3],
            "resigned_on": r[4],
            "nationality": r[5],
            "country_of_residence": r[6],
            "email": r[7],
        }
        for r in officers_rows
    ]
    pscs = [
        {
            "id": r[0],
            "name": r[1],
            "kind": r[2],
            "nationality": r[3],
            "country_of_residence": r[4],
            "natures_of_control": r[5] or [],
            "notified_on": r[6],
            "ceased_on": r[7],
            "email": r[8],
        }
        for r in pscs_rows
    ]
    research_links = build_contact_research_links(
        company_name=row[1],
        jurisdiction=row[2],
        source_ref=row[7],
        registered_address=row[5],
        verify_url=row[8],
        officers=officers,
    )
    lei_reverse_lookup = [
        {
            "lei_code": r[0],
            "legal_name": r[1],
            "registered_as": r[2],
            "entity_status": r[3],
            "registration_status": r[4],
            "registered_on": r[5],
            "lou_code": r[6],
            "gleif_url": r[7],
            "last_seen": r[8],
        }
        for r in lei_reverse_rows
    ]

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
                "website": row[9],
                "source_updated_at": row[28],
                "last_enriched_at": row[29],
            },
            "score": score,
            "action": action,
            "next_action": next_action,
            "contact_path": contact_path,
            "suggested_route": suggested_route,
            "research_links": research_links,
            "introducer_hint": route_hint,
            "company_contact": company_contact,
            "candidate_contact_routes": candidate_contact_routes,
            "verification_sources": verification_sources,
            "route_intelligence": route_intelligence,
            "introducer_matches": introducer_matches,
            "audit_rows": audit_rendered,
            "last_activity": last_activity,
            "lei": lei,
            "lei_reverse_lookup": lei_reverse_lookup,
            "officers": officers,
            "pscs": pscs,
            "rm_names": RM_NAMES,
            "statuses": _STATUSES,
            "saved": False,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
            "signal_details": SIGNAL_DETAILS,
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
    if status not in _STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    if assigned_to and assigned_to not in RM_NAMES:
        raise HTTPException(status_code=400, detail="Unknown assignee")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT assigned_to, status, notes, contacted_at, follow_up_at FROM rm_actions WHERE company_id = %s",
                (lead_id,),
            )
            existing = cur.fetchone()
            old_value = (
                None
                if existing is None
                else {
                    "assigned_to": existing[0],
                    "status": existing[1],
                    "notes": existing[2],
                    "contacted_at": existing[3].isoformat() if existing[3] else None,
                    "follow_up_at": existing[4].isoformat() if existing[4] else None,
                }
            )

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
                (
                    lead_id,
                    assigned_to or None,
                    status,
                    notes or None,
                    contacted_at,
                    follow_up_at,
                ),
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
                (
                    lead_id,
                    (_read_actor(request) or "unknown"),
                    Jsonb(old_value),
                    Jsonb(new_value),
                ),
            )
            conn.commit()

    return _render_action_panel(
        lead_id,
        assigned_to or "",
        status,
        notes,
        None if not contacted_at else datetime.strptime(contacted_at, "%Y-%m-%d"),
        None if not follow_up_at else datetime.strptime(follow_up_at, "%Y-%m-%d"),
        saved=True,
    )


@app.post("/leads/{lead_id}/person-email", response_class=HTMLResponse)
def lead_person_email_update(
    request: Request,
    lead_id: UUID,
    person_kind: str = Form(...),
    person_id: UUID = Form(...),
    email: str = Form(""),
):
    normalized_email = email.strip() or None

    table_name = None
    id_column = None
    if person_kind == "officer":
        table_name = "company_officers"
        id_column = "id"
    elif person_kind == "psc":
        table_name = "company_pscs"
        id_column = "id"
    else:
        raise HTTPException(status_code=400, detail="Unsupported person kind")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table_name} SET email = %s WHERE {id_column} = %s AND company_id = %s",
                (normalized_email, person_id, lead_id),
            )
        conn.commit()

    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@app.post("/leads/{lead_id}/website", response_class=HTMLResponse)
def lead_website_update(request: Request, lead_id: UUID, website: str = Form("")):
    normalized_website = website.strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE companies SET website = %s, updated_at = NOW() WHERE id = %s",
                (normalized_website, lead_id),
            )
        conn.commit()

    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@app.post("/leads/{lead_id}/contact-research", response_class=HTMLResponse)
def lead_contact_research_update(
    request: Request,
    lead_id: UUID,
    website: str = Form(""),
    generic_email: str = Form(""),
    contact_form_url: str = Form(""),
    linkedin_url: str = Form(""),
    source: str = Form(""),
    confidence: str = Form(""),
    last_checked: str = Form(""),
    checked_by: str = Form(""),
):
    confidence_values = {"": None, "Low": 0.33, "Medium": 0.66, "High": 1.0}
    if confidence not in confidence_values:
        raise HTTPException(status_code=400, detail="Invalid confidence")

    cleaned = {
        "website": website.strip(),
        "generic_email": generic_email.strip(),
        "contact_form_url": contact_form_url.strip(),
        "linkedin_url": linkedin_url.strip(),
        "source": source.strip(),
        "confidence": confidence,
        "last_checked": last_checked.strip(),
        "checked_by": (_read_actor(request) or checked_by.strip()),
    }
    for field in ("website", "contact_form_url", "linkedin_url"):
        value = cleaned[field]
        if value and not re.match(r"^https?://", value, flags=re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if cleaned["generic_email"] and not re.match(
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned["generic_email"]
    ):
        raise HTTPException(status_code=400, detail="Invalid email")
    has_contact_value = any(
        cleaned[field]
        for field in ("website", "generic_email", "contact_form_url", "linkedin_url")
    )
    if has_contact_value:
        missing_provenance = [
            label
            for field, label in (
                ("source", "source"),
                ("confidence", "confidence"),
                ("last_checked", "last checked"),
                ("checked_by", "checked by"),
            )
            if not cleaned[field]
        ]
        if missing_provenance:
            raise HTTPException(
                status_code=400,
                detail=f"Contact research requires {', '.join(missing_provenance)}",
            )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT website, generic_email, contact_form_url, linkedin_url,
                       source, confidence, verified_at, checked_by
                FROM company_contacts WHERE company_id = %s
                """,
                (lead_id,),
            )
            old_row = cur.fetchone()
            old_value = (
                None
                if old_row is None
                else {
                    "website": old_row[0],
                    "generic_email": old_row[1],
                    "contact_form_url": old_row[2],
                    "linkedin_url": old_row[3],
                    "source": old_row[4],
                    "confidence": float(old_row[5]) if old_row[5] is not None else None,
                    "last_checked": old_row[6].isoformat() if old_row[6] else None,
                    "checked_by": old_row[7],
                }
            )
            cur.execute(
                """
                INSERT INTO company_contacts (
                    company_id, website, generic_email, contact_form_url,
                    linkedin_url, source, confidence, verified_at, checked_by
                )
                VALUES (
                    %s, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''),
                    NULLIF(%s, ''), NULLIF(%s, ''), %s,
                    NULLIF(%s, '')::date, NULLIF(%s, '')
                )
                ON CONFLICT (company_id) DO UPDATE SET
                    website = EXCLUDED.website,
                    generic_email = EXCLUDED.generic_email,
                    contact_form_url = EXCLUDED.contact_form_url,
                    linkedin_url = EXCLUDED.linkedin_url,
                    source = EXCLUDED.source,
                    confidence = EXCLUDED.confidence,
                    verified_at = EXCLUDED.verified_at,
                    checked_by = EXCLUDED.checked_by,
                    updated_at = NOW()
                """,
                (
                    lead_id,
                    cleaned["website"],
                    cleaned["generic_email"],
                    cleaned["contact_form_url"],
                    cleaned["linkedin_url"],
                    cleaned["source"],
                    confidence_values[confidence],
                    cleaned["last_checked"],
                    cleaned["checked_by"],
                ),
            )
            cur.execute(
                "UPDATE companies SET website = NULLIF(%s, ''), updated_at = NOW() WHERE id = %s",
                (cleaned["website"], lead_id),
            )
            cur.execute(
                """
                INSERT INTO audit_log (
                    entity_type, entity_id, action, actor, old_value, new_value, ip_address
                )
                VALUES ('company', %s, 'contact_research_updated', %s, %s, %s, NULL)
                """,
                (
                    lead_id,
                    (_read_actor(request) or cleaned["checked_by"] or "unknown"),
                    Jsonb(old_value),
                    Jsonb(cleaned),
                ),
            )
        conn.commit()

    return RedirectResponse(url=f"/leads/{lead_id}#contact-research", status_code=303)


def _review_contact_suggestion(
    request: Request,
    lead_id: UUID,
    suggestion_id: UUID,
    decision: str,
) -> RedirectResponse:
    actor = _read_actor(request)
    if not actor:
        raise HTTPException(status_code=400, detail="Select Acting as before review")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT suggestion_type, suggested_value, source_name, source_url,
                       search_query, confidence, confidence_reason, status
                FROM contact_discovery_suggestions
                WHERE id = %s AND company_id = %s
                FOR UPDATE
                """,
                (suggestion_id, lead_id),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            suggestion = {
                "suggestion_type": row[0],
                "suggested_value": row[1],
                "source_name": row[2],
                "source_url": row[3],
                "search_query": row[4],
                "confidence": row[5],
                "confidence_reason": row[6],
                "status": row[7],
            }
            try:
                new_status = review_status(suggestion["status"], decision)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            if (
                decision == "Accepted"
                and suggestion_category(
                    suggestion["suggestion_type"], suggestion["suggested_value"]
                )
                != "candidate"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Only a concrete contact-route candidate can be accepted",
                )

            target = None
            if decision == "Accepted":
                cur.execute(
                    """
                    SELECT website, generic_email, contact_form_url, linkedin_url,
                           source, confidence, verified_at, checked_by
                    FROM company_contacts WHERE company_id = %s
                    """,
                    (lead_id,),
                )
                contact_row = cur.fetchone()
                existing = {
                    "website": contact_row[0] if contact_row else None,
                    "generic_email": contact_row[1] if contact_row else None,
                    "contact_form_url": contact_row[2] if contact_row else None,
                    "linkedin_url": contact_row[3] if contact_row else None,
                    "source": contact_row[4] if contact_row else None,
                    "confidence": str(contact_row[5]) if contact_row and contact_row[5] is not None else None,
                    "verified_at": contact_row[6].isoformat() if contact_row and contact_row[6] else None,
                    "checked_by": contact_row[7] if contact_row else None,
                }
                merged, target = prepare_contact_acceptance(
                    existing=existing,
                    suggestion=suggestion,
                    reviewer=actor,
                )
                if target:
                    cur.execute(
                        """
                        INSERT INTO company_contacts (
                            company_id, website, generic_email, contact_form_url,
                            linkedin_url, source, confidence, verified_at, checked_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::date, %s)
                        ON CONFLICT (company_id) DO UPDATE SET
                            website = EXCLUDED.website,
                            generic_email = EXCLUDED.generic_email,
                            contact_form_url = EXCLUDED.contact_form_url,
                            linkedin_url = EXCLUDED.linkedin_url,
                            source = EXCLUDED.source,
                            confidence = EXCLUDED.confidence,
                            verified_at = EXCLUDED.verified_at,
                            checked_by = EXCLUDED.checked_by,
                            updated_at = NOW()
                        """,
                        (
                            lead_id,
                            merged.get("website"),
                            merged.get("generic_email"),
                            merged.get("contact_form_url"),
                            merged.get("linkedin_url"),
                            merged.get("source"),
                            merged.get("confidence"),
                            merged.get("verified_at"),
                            merged.get("checked_by"),
                        ),
                    )
                    if target == "website":
                        cur.execute(
                            "UPDATE companies SET website = %s, updated_at = NOW() WHERE id = %s",
                            (merged["website"], lead_id),
                        )

            cur.execute(
                """
                UPDATE contact_discovery_suggestions
                SET status = %s, reviewed_by = %s, reviewed_at = NOW(),
                    notes = %s
                WHERE id = %s
                """,
                (
                    new_status,
                    actor,
                    (
                        f"Accepted into Contact Research field: {target}"
                        if target
                        else "Accepted as a candidate route; no contact field populated"
                        if decision == "Accepted"
                        else "Rejected during RM review"
                    ),
                    suggestion_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_log (
                    entity_type, entity_id, action, actor, old_value, new_value, ip_address
                ) VALUES ('company', %s, %s, %s, %s, %s, NULL)
                """,
                (
                    lead_id,
                    "contact_suggestion_accepted" if decision == "Accepted" else "contact_suggestion_rejected",
                    actor,
                    Jsonb({"suggestion_id": str(suggestion_id), "status": suggestion["status"]}),
                    Jsonb(
                        {
                            "suggestion_id": str(suggestion_id),
                            "status": new_status,
                            "suggestion_type": suggestion["suggestion_type"],
                            "suggested_value": suggestion["suggested_value"],
                            "contact_field": target,
                        }
                    ),
                ),
            )
        conn.commit()
    return RedirectResponse(url=f"/leads/{lead_id}#candidate-contact-routes", status_code=303)


@app.post("/leads/{lead_id}/contact-suggestions/{suggestion_id}/accept")
def accept_contact_suggestion(
    request: Request, lead_id: UUID, suggestion_id: UUID
):
    return _review_contact_suggestion(
        request, lead_id, suggestion_id, decision="Accepted"
    )


@app.post("/leads/{lead_id}/contact-suggestions/{suggestion_id}/reject")
def reject_contact_suggestion(
    request: Request, lead_id: UUID, suggestion_id: UUID
):
    return _review_contact_suggestion(
        request, lead_id, suggestion_id, decision="Rejected"
    )


def _review_route_recommendation(
    request: Request,
    lead_id: UUID,
    recommendation_id: UUID,
    decision: str,
) -> RedirectResponse:
    actor = _read_actor(request)
    if not actor:
        raise HTTPException(status_code=400, detail="Select Acting as before review")
    if decision not in {"accepted", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid route decision")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, route_candidate_id, contactability_bucket,
                       best_route_type, best_route_value, evidence_summary
                FROM route_recommendations
                WHERE id = %s AND lead_id = %s
                FOR UPDATE
                """,
                (recommendation_id, lead_id),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Route recommendation not found")
            if row[0] != "suggested":
                raise HTTPException(status_code=409, detail="Route has already been reviewed")
            cur.execute(
                """
                UPDATE route_recommendations
                SET status = %s, reviewed_by = %s, reviewed_at = NOW()
                WHERE id = %s
                """,
                (decision, actor, recommendation_id),
            )
            if row[1]:
                cur.execute(
                    """
                    UPDATE introducer_matches
                    SET status = %s, reviewed_by = %s, reviewed_at = NOW()
                    WHERE lead_id = %s AND introducer_id = %s AND status = 'pending'
                    """,
                    (decision, actor, lead_id, row[1]),
                )
            cur.execute(
                """
                INSERT INTO audit_log (
                    entity_type, entity_id, action, actor,
                    old_value, new_value, ip_address
                ) VALUES ('company', %s, %s, %s, %s, %s, NULL)
                """,
                (
                    lead_id,
                    f"route_recommendation_{decision}",
                    actor,
                    Jsonb({"status": row[0]}),
                    Jsonb(
                        {
                            "status": decision,
                            "contactability_bucket": row[2],
                            "best_route_type": row[3],
                            "best_route_value": row[4],
                            "evidence_summary": row[5] or [],
                        }
                    ),
                ),
            )
        conn.commit()
    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@app.post("/leads/{lead_id}/route-recommendations/{recommendation_id}/accept")
def accept_route_recommendation(
    request: Request, lead_id: UUID, recommendation_id: UUID
):
    return _review_route_recommendation(request, lead_id, recommendation_id, "accepted")


@app.post("/leads/{lead_id}/route-recommendations/{recommendation_id}/reject")
def reject_route_recommendation(
    request: Request, lead_id: UUID, recommendation_id: UUID
):
    return _review_route_recommendation(request, lead_id, recommendation_id, "rejected")


@app.post("/leads/{lead_id}/contacts", response_class=HTMLResponse)
def add_lead_contact(
    request: Request,
    lead_id: UUID,
    name: str = Form(...),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    linkedin_url: str = Form(""),
    source: str = Form("manual"),
    notes: str = Form(""),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_contacts
                    (company_id, name, role, email, phone, linkedin_url, source, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(lead_id),
                    name.strip(),
                    role.strip(),
                    email.strip(),
                    phone.strip(),
                    linkedin_url.strip(),
                    source,
                    notes.strip(),
                ),
            )
        conn.commit()
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@app.post("/leads/{lead_id}/contacts/{contact_id}/delete", response_class=HTMLResponse)
def delete_lead_contact(request: Request, lead_id: UUID, contact_id: UUID):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lead_contacts WHERE id = %s AND company_id = %s",
                (str(contact_id), str(lead_id)),
            )
        conn.commit()
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


# --- Inline assign/status endpoint for queue HTMX ---
@app.post("/leads/{lead_id}/assign", response_class=HTMLResponse)
def lead_assign(
    request: Request,
    lead_id: UUID,
    assigned_to: str = Form(""),
    status: str = Form("New"),
):
    if status not in _STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    if assigned_to and assigned_to not in RM_NAMES:
        raise HTTPException(status_code=400, detail="Unknown assignee")
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
                (
                    lead_id,
                    (_read_actor(request) or "unknown"),
                    Jsonb({"assigned_to": assigned_to or None, "status": status}),
                ),
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
    }

    score_pct = r["priority_score"]
    score_color = (
        "#059669" if score_pct >= 70 else "#d97706" if score_pct >= 40 else "#d1d5db"
    )
    assigned_opts = "".join(
        f'<option value="{nm}" {"selected" if nm == (r["assigned_to"] or "") else ""}>{nm}</option>'
        for nm in RM_NAMES
    )
    status_opts = "".join(
        f'<option value="{s}" {"selected" if s == (r["status"] or "New") else ""}>{s}</option>'
        for s in _STATUSES
    )
    verify = (
        f'<a href="{r["verify_url"]}" target="_blank" style="font-size:11px">↗</a>'
        if r["verify_url"]
        else ""
    )

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
    result = None
    qp = request.query_params
    if "inserted" in qp:

        def _safe_int(value: str) -> int:
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                return 0

        result = {
            "inserted": _safe_int(qp.get("inserted", "0")),
            "skipped": _safe_int(qp.get("skipped", "0")),
            "scored": _safe_int(qp.get("scored", "0")),
        }
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "preview": None,
            "result": result,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
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
        context={
            "preview": preview,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
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
                raise HTTPException(
                    status_code=409, detail="Upload already confirmed or rejected"
                )

            parsed_rows, validation_errors = claimed
            if validation_errors:
                # Roll back the status change — this upload has errors and cannot be confirmed
                conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail="Upload has validation errors and cannot be confirmed",
                )

            parsed_rows = parsed_rows or []
            inserted = 0
            skipped_duplicates = 0
            for index, row in enumerate(parsed_rows, start=1):
                company_name = (row.get("company_name") or "").strip()
                jurisdiction = (row.get("jurisdiction") or "").strip()
                entity_type = (row.get("entity_type") or "").strip() or None
                website = (row.get("website") or "").strip() or None
                normalised_name = re.sub(
                    r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", company_name.lower())
                ).strip()
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

        # Score the freshly-inserted companies and rebuild the queue snapshot so
        # uploaded leads are visible/actionable immediately (not only after the
        # nightly run). Deterministic scoring only — same code path as the pipeline.
        scored = 0
        if inserted:
            from src.pipeline import _refresh_queue, _score_new_companies

            scored = _score_new_companies(conn)
            _refresh_queue(conn)

    return RedirectResponse(
        url=f"/upload?inserted={inserted}&skipped={skipped_duplicates}&scored={scored}",
        status_code=303,
    )


_AUDIT_ACTION_LABELS = {
    "rm_action_updated": "Lead updated",
    "quick_assign": "Quick assign",
    "lead_status_changed": "Status changed",
    "lead_assigned": "Reassigned",
    "score_recalculated": "Score recalculated",
    "contact_research_updated": "Contact research updated",
    "contact_suggestion_accepted": "Contact suggestion accepted",
    "contact_suggestion_rejected": "Contact suggestion rejected",
    "route_recommendation_accepted": "Route recommendation accepted",
    "route_recommendation_rejected": "Route recommendation rejected",
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
        return [
            {
                "label": "Value",
                "old": _format_audit_value(old),
                "new": _format_audit_value(new),
            }
        ]
    old_dict = old if isinstance(old, dict) else {}
    changes = []
    # union of keys preserves all transitions, including new keys
    for key in new.keys():
        old_v = old_dict.get(key)
        new_v = new.get(key)
        if old_v == new_v:
            continue
        changes.append(
            {
                "label": _audit_field_label(key),
                "old": _format_audit_value(old_v),
                "new": _format_audit_value(new_v),
            }
        )
    # if nothing changed but it's still a meaningful event, show a compact summary
    if not changes:
        for key, val in new.items():
            if val in (None, ""):
                continue
            changes.append(
                {
                    "label": _audit_field_label(key),
                    "old": None,
                    "new": _format_audit_value(val),
                }
            )
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

            cur.execute(
                "SELECT DISTINCT actor FROM audit_log WHERE actor IS NOT NULL ORDER BY 1"
            )
            actor_options = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT action FROM audit_log WHERE action IS NOT NULL ORDER BY 1"
            )
            action_options = [r[0] for r in cur.fetchall()]

    total_pages = max((total + page_size - 1) // page_size, 1)
    rendered_rows = []
    for row in rows:
        (
            entity_type,
            entity_id,
            action,
            actor,
            old_v,
            new_v,
            created_at,
            company_name,
        ) = row
        rendered_rows.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "entity_url": (
                    f"/leads/{entity_id}" if entity_type == "company" else None
                ),
                "entity_label": company_name
                or (str(entity_id)[:8] if entity_id else "—"),
                "action": action,
                "action_label": _audit_action_label(action),
                "actor": _humanize_actor(actor),
                "actor_raw": actor or "",
                "changes": _audit_changes(old_v, new_v),
                "created_at": created_at,
                "created_at_iso": created_at.isoformat() if created_at else "",
                "created_at_display": (
                    created_at.strftime("%d %b %Y %H:%M") if created_at else ""
                ),
                "created_at_relative": _relative_time(created_at),
            }
        )

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
