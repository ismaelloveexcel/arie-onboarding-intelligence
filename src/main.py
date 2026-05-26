import csv
import io
import logging
import re
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Jsonb
from pythonjsonlogger import jsonlogger

from src.config import ACTOR_NAMES, APP_ENV, LOG_LEVEL, RM_NAMES
from src.db import check_connection, get_conn
from src.scoring import SCORING_VERSION

# --- Logging setup ---
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.handlers = [handler]
root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Arie Leads",
    docs_url=None if APP_ENV == "production" else "/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")

_STATUSES = ["New", "Reviewing", "Qualified", "Not Relevant", "Deferred", "Contacted", "Onboarding", "Not Fit"]
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


@app.post("/me")
def set_actor(request: Request, actor: str = Form("")):
    referer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referer, status_code=303)
    if actor:
        response.set_cookie("actor", actor, max_age=30 * 24 * 3600, httponly=True, secure=True, samesite="lax")
    else:
        response.delete_cookie("actor")
    return response


@app.get("/", response_class=HTMLResponse)
def queue(request: Request):
    filters = {
        "tier": request.query_params.get("tier", ""),
        "jurisdiction": request.query_params.get("jurisdiction", ""),
        "assigned_to": request.query_params.get("assigned_to", ""),
        "status": request.query_params.get("status", ""),
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

    sort_sql = {
        "score": "qs.priority_score DESC, c.company_name ASC",
        "date": "c.incorporation_date DESC NULLS LAST, qs.priority_score DESC, c.company_name ASC",
        "name": "c.company_name ASC",
    }.get(filters["sort"], "qs.priority_score DESC, c.company_name ASC")

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
            "entity_type": row[3],
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
            "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
            "filters": filters,
            "rm_names": RM_NAMES,
            "statuses": _STATUSES,
            "page": page,
            "total_pages": total_pages,
            "query_string": _build_query_string(query_params),
            "actor_names": ACTOR_NAMES,
            "current_actor": request.cookies.get("actor", ""),
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
        "notes": row[16],
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

    return templates.TemplateResponse(
        request=request,
        name="lead_detail.html",
        context={
            "lead": {
                "id": row[0],
                "company_name": row[1],
                "jurisdiction": row[2],
                "entity_type": row[3],
                "incorporation_date": row[4],
                "registered_address": row[5],
                "source_system": row[6],
                "source_ref": row[7],
                "verify_url": row[8],
            },
            "score": score,
            "action": action,
            "audit_rows": audit_rendered,
            "rm_names": RM_NAMES,
            "statuses": _STATUSES,
            "saved": False,
            "actor_names": ACTOR_NAMES,
            "current_actor": request.cookies.get("actor", ""),
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
                (lead_id, request.cookies.get("actor", "unknown"), Jsonb(old_value), Jsonb(new_value)),
            )
            conn.commit()

    return _render_action_panel(lead_id, assigned_to or "", status, notes, None if not contacted_at else datetime.strptime(contacted_at, "%Y-%m-%d"), None if not follow_up_at else datetime.strptime(follow_up_at, "%Y-%m-%d"), saved=True)


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"preview": None, "actor_names": ACTOR_NAMES, "current_actor": request.cookies.get("actor", "")},
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
                    request.cookies.get("actor", "unknown"),
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
        context={"preview": preview, "actor_names": ACTOR_NAMES, "current_actor": request.cookies.get("actor", "")},
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
            for index, row in enumerate(parsed_rows, start=1):
                company_name = (row.get("company_name") or "").strip()
                jurisdiction = (row.get("jurisdiction") or "").strip()
                entity_type = (row.get("entity_type") or "").strip() or None
                website = (row.get("website") or "").strip() or None
                normalised_name = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", company_name.lower())).strip()
                source_ref = f"upload:{upload_id}:{index}"
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

            conn.commit()

    return RedirectResponse(url="/", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    page_size = 50

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_log")
            total = cur.fetchone()[0]
            cur.execute(
                """
                SELECT entity_type, entity_id, action, actor, old_value, new_value, created_at
                FROM audit_log
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, (page - 1) * page_size),
            )
            rows = cur.fetchall()

    total_pages = max((total + page_size - 1) // page_size, 1)
    rendered_rows = [
        {
            "entity_type": row[0],
            "entity_id": row[1],
            "action": row[2],
            "actor": row[3],
            "old_value": row[4],
            "new_value": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]

    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "rows": rendered_rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "actor_names": ACTOR_NAMES,
            "current_actor": request.cookies.get("actor", ""),
        },
    )

