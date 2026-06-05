"""Introducers feature: list/detail/upload routes + HTMX inline assign.

Mirrors the direct-clients flow (queue + lead_detail + upload). Wired into
src/main.py via include_router(...).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Jsonb

from src.config import ACTOR_NAMES, RM_NAMES
from src.db import get_conn
from src.domain.statuses import normalize_status, require_canonical_status, status_label, status_options
from src.security.write_auth import write_guard_required
from src.security.url_safety import sanitize_external_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/introducers", tags=["introducers"])
templates = Jinja2Templates(directory="src/templates")

_STATUS_OPTIONS = status_options()


def _canonical_status_or_422(raw_status: str) -> str:
    try:
        return require_canonical_status(raw_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

_UPLOAD_REQUIRED_COLUMNS = ["company_name", "jurisdiction"]
_UPLOAD_MAX_ROWS = 20000
_UPLOAD_MAX_BYTES = 15 * 1024 * 1024

_ACTOR_COOKIE = "actor"


def _read_actor(request: Request) -> str:
    # Lazy import so we don't cycle with main.
    from src.main import _read_actor as _ra
    return _ra(request)


def _build_qs(params: dict) -> str:
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (name or "").lower())).strip()


def _parse_introducer_csv(file_bytes: bytes) -> tuple[list[str], list[dict], list[str]]:
    errors: list[str] = []
    if len(file_bytes) > _UPLOAD_MAX_BYTES:
        return [], [], [f"File exceeds {_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit."]
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [], ["CSV must be UTF-8 encoded."]

    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    if not columns:
        return [], [], ["CSV header row is missing."]
    missing = [c for c in _UPLOAD_REQUIRED_COLUMNS if c not in columns]
    if missing:
        errors.append("Missing required columns: " + ", ".join(missing))

    rows: list[dict] = []
    for index, row in enumerate(reader, start=1):
        if index > _UPLOAD_MAX_ROWS:
            errors.append(f"CSV exceeds {_UPLOAD_MAX_ROWS} row limit.")
            break
        cleaned = {k: (v or "").strip() for k, v in row.items() if k}
        if not cleaned.get("company_name"):
            errors.append(f"Row {index}: company_name is required.")
        if not cleaned.get("jurisdiction"):
            errors.append(f"Row {index}: jurisdiction is required.")
        rows.append(cleaned)
    return columns, rows, errors


# ---------------------------------------------------------------- LIST
@router.get("", response_class=HTMLResponse)
def introducers_list(request: Request):
    filters = {
        "jurisdiction": request.query_params.get("jurisdiction", ""),
        "assigned_to": request.query_params.get("assigned_to", ""),
        "status": request.query_params.get("status", ""),
        "q": request.query_params.get("q", ""),
        "sort": request.query_params.get("sort", "name"),
    }
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    page_size = 50

    where = ["1=1"]
    params: list[object] = []
    if filters["jurisdiction"]:
        where.append("i.jurisdiction = %s")
        params.append(filters["jurisdiction"])
    if filters["assigned_to"]:
        where.append("ia.assigned_to = %s")
        params.append(filters["assigned_to"])
    if filters["status"]:
        status_filter = normalize_status(filters["status"])
        if status_filter:
            where.append("ia.status = %s")
            params.append(status_filter)
    if filters["q"]:
        where.append("(i.company_name ILIKE %s OR i.contact_name ILIKE %s OR i.contact_email ILIKE %s)")
        like = f"%{filters['q']}%"
        params.extend([like, like, like])

    sort_sql = {
        "name": "i.company_name ASC",
        "recent": "i.created_at DESC, i.company_name ASC",
        "jurisdiction": "i.jurisdiction ASC, i.company_name ASC",
    }.get(filters["sort"], "i.company_name ASC")

    count_sql = f"""
        SELECT COUNT(*) FROM introducers i
        LEFT JOIN introducer_actions ia ON ia.introducer_id = i.id
        WHERE {' AND '.join(where)}
    """
    list_sql = f"""
        SELECT i.id, i.company_name, i.jurisdiction, i.entity_type,
               i.contact_name, i.contact_email, i.phone_number, i.verify_url,
               ia.assigned_to, ia.status
        FROM introducers i
        LEFT JOIN introducer_actions ia ON ia.introducer_id = i.id
        WHERE {' AND '.join(where)}
        ORDER BY {sort_sql}
        LIMIT %s OFFSET %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]
            cur.execute(list_sql, params + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()

    rendered = [
        {
            "id": r[0], "company_name": r[1], "jurisdiction": r[2],
            "entity_type": r[3] or "—",
            "contact_name": r[4], "contact_email": r[5],
            "phone_number": r[6], "verify_url": sanitize_external_url(r[7]),
            "assigned_to": r[8], "status": normalize_status(r[9]) or "new",
            "status_label": status_label(r[9]),
        }
        for r in rows
    ]
    total_pages = max((total + page_size - 1) // page_size, 1)

    return templates.TemplateResponse(
        request=request,
        name="introducers_queue.html",
        context={
            "rows": rendered,
            "total": total,
            "filters": filters,
            "rm_names": RM_NAMES,
            "statuses": _STATUS_OPTIONS,
            "page": page,
            "total_pages": total_pages,
            "query_string": _build_qs({k: v for k, v in filters.items() if v}),
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


# ---------------------------------------------------------------- DETAIL
@router.get("/{introducer_id}", response_class=HTMLResponse)
def introducer_detail(request: Request, introducer_id: UUID, saved: int = 0):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, i.company_name, i.jurisdiction, i.entity_type,
                       i.incorporation_date, i.source, i.company_number, i.file_no,
                       i.sic_codes, i.verify_url, i.contact_email, i.phone_number,
                       i.contact_name, i.address, i.notes,
                       ia.assigned_to, ia.status, ia.notes, ia.contacted_at, ia.follow_up_at
                FROM introducers i
                LEFT JOIN introducer_actions ia ON ia.introducer_id = i.id
                WHERE i.id = %s
                """,
                (introducer_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Introducer not found")

            cur.execute(
                """
                SELECT action, actor, old_value, new_value, created_at
                FROM audit_log
                WHERE entity_type = 'introducer' AND entity_id = %s
                ORDER BY created_at DESC LIMIT 50
                """,
                (introducer_id,),
            )
            audit_rows = [
                {"action": a[0], "actor": a[1], "old_value": a[2], "new_value": a[3], "created_at": a[4]}
                for a in cur.fetchall()
            ]

    introducer = {
        "id": row[0], "company_name": row[1], "jurisdiction": row[2],
        "entity_type": row[3], "incorporation_date": row[4],
        "source": row[5], "company_number": row[6], "file_no": row[7],
        "sic_codes": row[8], "verify_url": sanitize_external_url(row[9]),
        "contact_email": row[10], "phone_number": row[11],
        "contact_name": row[12], "address": row[13], "notes": row[14],
    }
    action = {
        "assigned_to": row[15],
        "status": normalize_status(row[16]) or "new",
        "notes": row[17],
        "contacted_at": row[18],
        "follow_up_at": row[19],
    }
    return templates.TemplateResponse(
        request=request,
        name="introducer_detail.html",
        context={
            "introducer": introducer,
            "action": action,
            "audit_rows": audit_rows,
            "rm_names": RM_NAMES,
            "statuses": _STATUS_OPTIONS,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
            "saved": False,
            "details_saved": bool(saved),
        },
    )


# ---------------------------------------------------------------- SAVE (full)
def _render_introducer_action_panel(introducer_id: UUID, assigned_to: str, status: str,
                                    notes: str | None, contacted_at, follow_up_at, saved: bool = False) -> HTMLResponse:
    rm_opts = "".join(
        f'<option value="{escape(rm)}" {"selected" if rm == assigned_to else ""}>{escape(rm)}</option>'
        for rm in RM_NAMES
    )
    status_opts = "".join(
        f'<option value="{escape(s["value"])}" {"selected" if s["value"] == status else ""}>{escape(s["label"])}</option>'
        for s in _STATUS_OPTIONS
    )
    contacted_val = contacted_at.date().isoformat() if hasattr(contacted_at, "date") and contacted_at else ""
    follow_val = follow_up_at.isoformat() if hasattr(follow_up_at, "isoformat") and follow_up_at else ""
    saved_html = '<div style="color:#065f46;font-size:12px;margin-top:.25rem">✓ Saved</div>' if saved else ""
    return HTMLResponse(f"""
    <div class="card" id="action-panel">
      <h2>RM Actions</h2>
      <form hx-post="/introducers/{introducer_id}/action" hx-target="#action-panel" hx-swap="outerHTML"
            style="display:flex;flex-direction:column;gap:.75rem">
        <div>
          <label>Assign To</label>
          <select name="assigned_to">
            <option value="">— Unassigned —</option>
            {rm_opts}
          </select>
        </div>
        <div>
          <label>Status</label>
          <select name="status">{status_opts}</select>
        </div>
        <div>
          <label>Notes</label>
          <textarea name="notes">{escape(notes or "")}</textarea>
        </div>
        <div class="grid-2">
          <div><label>Contacted At</label>
            <input type="date" name="contacted_at" value="{contacted_val}"></div>
          <div><label>Follow Up</label>
            <input type="date" name="follow_up_at" value="{follow_val}"></div>
        </div>
        <button type="submit" class="btn btn-primary">Save</button>
        {saved_html}
      </form>
    </div>""")


@router.post("/{introducer_id}/action", response_class=HTMLResponse)
@write_guard_required
def introducer_action(
    request: Request,
    introducer_id: UUID,
    assigned_to: str = Form(""),
    status: str = Form("new"),
    notes: str = Form(""),
    contacted_at: str = Form(""),
    follow_up_at: str = Form(""),
):
    status_canonical = _canonical_status_or_422(status)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT assigned_to, status, notes, contacted_at, follow_up_at FROM introducer_actions WHERE introducer_id = %s",
                (introducer_id,),
            )
            existing = cur.fetchone()
            old_value = None if existing is None else {
                "assigned_to": existing[0], "status": existing[1], "notes": existing[2],
                "contacted_at": existing[3].isoformat() if existing[3] else None,
                "follow_up_at": existing[4].isoformat() if existing[4] else None,
            }
            cur.execute(
                """
                INSERT INTO introducer_actions (introducer_id, assigned_to, status, notes, contacted_at, follow_up_at)
                VALUES (%s, %s, %s, %s, NULLIF(%s,'')::timestamptz, NULLIF(%s,'')::date)
                ON CONFLICT (introducer_id) DO UPDATE SET
                    assigned_to = EXCLUDED.assigned_to,
                    status = EXCLUDED.status,
                    notes = EXCLUDED.notes,
                    contacted_at = EXCLUDED.contacted_at,
                    follow_up_at = EXCLUDED.follow_up_at,
                    updated_at = NOW()
                """,
                (introducer_id, assigned_to or None, status_canonical, notes or None, contacted_at, follow_up_at),
            )
            new_value = {
                "assigned_to": assigned_to or None, "status": status_canonical,
                "notes": notes or None,
                "contacted_at": contacted_at or None,
                "follow_up_at": follow_up_at or None,
            }
            cur.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, actor, old_value, new_value)
                VALUES ('introducer', %s, 'introducer_action_updated', %s, %s, %s)
                """,
                (introducer_id, (_read_actor(request) or "unknown"),
                 Jsonb(old_value), Jsonb(new_value)),
            )
            conn.commit()

    contacted_dt = datetime.strptime(contacted_at, "%Y-%m-%d") if contacted_at else None
    follow_dt = datetime.strptime(follow_up_at, "%Y-%m-%d").date() if follow_up_at else None
    return _render_introducer_action_panel(
        introducer_id, assigned_to or "", status_canonical, notes, contacted_dt, follow_dt, saved=True,
    )


# ---------------------------------------------------------------- EDIT DETAILS
@router.post("/{introducer_id}/edit", response_class=HTMLResponse)
@write_guard_required
def introducer_edit(
    request: Request,
    introducer_id: UUID,
    company_name: str = Form(""),
    jurisdiction: str = Form(""),
    entity_type: str = Form(""),
    incorporation_date: str = Form(""),
    company_number: str = Form(""),
    file_no: str = Form(""),
    sic_codes: str = Form(""),
    verify_url: str = Form(""),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    phone_number: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT company_name, jurisdiction, entity_type, incorporation_date,
                       company_number, file_no, sic_codes, verify_url,
                       contact_name, contact_email, phone_number, address, notes
                FROM introducers WHERE id = %s
                """,
                (introducer_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Introducer not found")

            new_company = (company_name or "").strip() or existing[0]
            new_jx = (jurisdiction or "").strip() or existing[1]
            cur.execute(
                """
                UPDATE introducers SET
                    company_name = %s,
                    normalised_name = %s,
                    jurisdiction = %s,
                    entity_type = NULLIF(%s, ''),
                    incorporation_date = NULLIF(%s, '')::date,
                    company_number = NULLIF(%s, ''),
                    file_no = NULLIF(%s, ''),
                    sic_codes = NULLIF(%s, ''),
                    verify_url = NULLIF(%s, ''),
                    contact_name = NULLIF(%s, ''),
                    contact_email = NULLIF(%s, ''),
                    phone_number = NULLIF(%s, ''),
                    address = NULLIF(%s, ''),
                    notes = NULLIF(%s, ''),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    new_company, _normalise(new_company), new_jx,
                    entity_type.strip(), incorporation_date.strip(),
                    company_number.strip(), file_no.strip(), sic_codes.strip(),
                    sanitize_external_url(verify_url), contact_name.strip(), contact_email.strip(),
                    phone_number.strip(), address.strip(), notes.strip(),
                    introducer_id,
                ),
            )
            old_value = {
                "company_name": existing[0], "jurisdiction": existing[1],
                "entity_type": existing[2],
                "incorporation_date": existing[3].isoformat() if existing[3] else None,
                "company_number": existing[4], "file_no": existing[5],
                "sic_codes": existing[6], "verify_url": existing[7],
                "contact_name": existing[8], "contact_email": existing[9],
                "phone_number": existing[10], "address": existing[11], "notes": existing[12],
            }
            new_value = {
                "company_name": new_company, "jurisdiction": new_jx,
                "entity_type": entity_type.strip() or None,
                "incorporation_date": incorporation_date.strip() or None,
                "company_number": company_number.strip() or None,
                "file_no": file_no.strip() or None,
                "sic_codes": sic_codes.strip() or None,
                "verify_url": sanitize_external_url(verify_url),
                "contact_name": contact_name.strip() or None,
                "contact_email": contact_email.strip() or None,
                "phone_number": phone_number.strip() or None,
                "address": address.strip() or None,
                "notes": notes.strip() or None,
            }
            cur.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, actor, old_value, new_value)
                VALUES ('introducer', %s, 'introducer_details_updated', %s, %s, %s)
                """,
                (introducer_id, (_read_actor(request) or "unknown"),
                 Jsonb(old_value), Jsonb(new_value)),
            )
            conn.commit()

    response = HTMLResponse("")
    response.headers["HX-Redirect"] = f"/introducers/{introducer_id}?saved=1"
    return response


# ---------------------------------------------------------------- INLINE ASSIGN
@router.post("/{introducer_id}/assign", response_class=HTMLResponse)
@write_guard_required
def introducer_assign(
    request: Request,
    introducer_id: UUID,
    assigned_to: str = Form(""),
    status: str = Form("new"),
):
    status_canonical = _canonical_status_or_422(status)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO introducer_actions (introducer_id, assigned_to, status)
                VALUES (%s, %s, %s)
                ON CONFLICT (introducer_id) DO UPDATE SET
                    assigned_to = EXCLUDED.assigned_to,
                    status = EXCLUDED.status,
                    updated_at = NOW()
                """,
                (introducer_id, assigned_to or None, status_canonical),
            )
            cur.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, actor, new_value)
                VALUES ('introducer', %s, 'quick_assign', %s, %s)
                """,
                (
                    introducer_id,
                    (_read_actor(request) or "unknown"),
                    Jsonb({"assigned_to": assigned_to or None, "status": status_canonical}),
                ),
            )
            conn.commit()

            cur.execute(
                """
                SELECT i.id, i.company_name, i.jurisdiction, i.entity_type,
                       i.contact_name, i.contact_email, i.phone_number, i.verify_url,
                       ia.assigned_to, ia.status
                FROM introducers i
                LEFT JOIN introducer_actions ia ON ia.introducer_id = i.id
                WHERE i.id = %s
                """,
                (introducer_id,),
            )
            row = cur.fetchone()

    if row is None:
        return HTMLResponse("<tr><td colspan='8'>Not found</td></tr>", status_code=404)

    assigned_opts = "".join(
        f'<option value="{escape(nm)}" {"selected" if nm == (row[8] or "") else ""}>{escape(nm)}</option>'
        for nm in RM_NAMES
    )
    row_status = normalize_status(row[9]) or "new"
    status_opts = "".join(
        f'<option value="{escape(s["value"])}" {"selected" if s["value"] == row_status else ""}>{escape(s["label"])}</option>'
        for s in _STATUS_OPTIONS
    )
    verify = f'<a href="{escape(sanitize_external_url(row[7]) or "", quote=True)}" target="_blank" style="font-size:11px">↗</a>' if sanitize_external_url(row[7]) else ""
    email_html = f'<a href="mailto:{escape(row[5])}">{escape(row[5])}</a>' if row[5] else "—"

    return HTMLResponse(f"""
    <tr>
      <td><a href="/introducers/{row[0]}">{escape(row[1])}</a></td>
      <td>{escape(row[2])}</td>
      <td>{escape(row[3] or '—')}</td>
      <td>{escape(row[4] or '—')}</td>
      <td>{email_html}</td>
      <td>{escape(row[6] or '—')}</td>
      <td>
        <form hx-post="/introducers/{row[0]}/assign" hx-target="closest tr" hx-swap="outerHTML" style="margin:0">
          <input type="hidden" name="status" value="{escape(row_status)}">
          <select name="assigned_to" onchange="this.form.requestSubmit()"
                  style="font-size:11px;padding:2px 4px;border:1px solid #ddd;border-radius:4px;max-width:90px">
            <option value="">—</option>{assigned_opts}
          </select>
        </form>
      </td>
      <td>
        <form hx-post="/introducers/{row[0]}/assign" hx-target="closest tr" hx-swap="outerHTML" style="margin:0">
          <input type="hidden" name="assigned_to" value="{escape(row[8] or '')}">
          <select name="status" onchange="this.form.requestSubmit()"
                  style="font-size:11px;padding:2px 4px;border:1px solid #ddd;border-radius:4px;max-width:110px">
            {status_opts}
          </select>
        </form>
      </td>
      <td>{verify}</td>
    </tr>""")


# ---------------------------------------------------------------- UPLOAD
@router.get("/upload/new", response_class=HTMLResponse)
def introducer_upload_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="introducer_upload.html",
        context={
            "preview": None,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


@router.post("/upload", response_class=HTMLResponse)
@write_guard_required
def introducer_upload_preview(request: Request, file: UploadFile = File(...)):
    file_bytes = file.file.read()
    columns, rows, validation_errors = _parse_introducer_csv(file_bytes)

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
                INSERT INTO pending_introducer_uploads
                  (filename, uploaded_by, row_count, parsed_rows, validation_errors, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    file.filename or "introducers.csv",
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
        name="introducer_upload.html",
        context={
            "preview": preview,
            "actor_names": ACTOR_NAMES,
            "current_actor": (_read_actor(request) or ""),
        },
    )


@router.post("/upload/{upload_id}/confirm")
@write_guard_required
def introducer_upload_confirm(request: Request, upload_id: UUID):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_introducer_uploads
                SET status = 'confirmed'
                WHERE id = %s AND status = 'pending'
                RETURNING parsed_rows, validation_errors
                """,
                (upload_id,),
            )
            claimed = cur.fetchone()
            if claimed is None:
                cur.execute("SELECT 1 FROM pending_introducer_uploads WHERE id = %s", (upload_id,))
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Upload not found")
                raise HTTPException(status_code=409, detail="Upload already confirmed or rejected")

            parsed_rows, validation_errors = claimed
            if validation_errors:
                conn.rollback()
                raise HTTPException(status_code=400, detail="Upload has validation errors")

            parsed_rows = parsed_rows or []
            inserted = 0
            updated = 0
            for row in parsed_rows:
                company_name = (row.get("company_name") or "").strip()
                jurisdiction = (row.get("jurisdiction") or "").strip()
                if not company_name or not jurisdiction:
                    continue
                normalised = _normalise(company_name)
                inc_date = (row.get("incorporation_date") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO introducers (
                        company_name, normalised_name, jurisdiction, entity_type,
                        incorporation_date, source, company_number, file_no,
                        sic_codes, verify_url, contact_email, phone_number,
                        contact_name, address, notes, raw_data
                    ) VALUES (
                        %s, %s, %s, %s,
                        NULLIF(%s,'')::date, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (normalised_name, jurisdiction) DO UPDATE SET
                        company_name = EXCLUDED.company_name,
                        entity_type = EXCLUDED.entity_type,
                        incorporation_date = EXCLUDED.incorporation_date,
                        source = EXCLUDED.source,
                        company_number = EXCLUDED.company_number,
                        file_no = EXCLUDED.file_no,
                        sic_codes = EXCLUDED.sic_codes,
                        verify_url = EXCLUDED.verify_url,
                        contact_email = EXCLUDED.contact_email,
                        phone_number = EXCLUDED.phone_number,
                        contact_name = EXCLUDED.contact_name,
                        address = EXCLUDED.address,
                        notes = EXCLUDED.notes,
                        raw_data = EXCLUDED.raw_data,
                        updated_at = NOW()
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (
                        company_name, normalised, jurisdiction,
                        (row.get("entity_type") or "").strip() or None,
                        inc_date,
                        (row.get("source") or "").strip() or None,
                        (row.get("company_number") or "").strip() or None,
                        (row.get("file_no") or "").strip() or None,
                        (row.get("sic_codes") or "").strip() or None,
                        sanitize_external_url(row.get("verify_url")),
                        (row.get("contact_email") or "").strip() or None,
                        (row.get("phone_number") or "").strip() or None,
                        (row.get("contact_name") or "").strip() or None,
                        (row.get("address") or "").strip() or None,
                        (row.get("notes") or "").strip() or None,
                        Jsonb(row),
                    ),
                )
                result = cur.fetchone()
                if result and result[0]:
                    inserted += 1
                else:
                    updated += 1

            conn.commit()
            logger.info(
                "introducer_upload_confirmed",
                extra={"upload_id": str(upload_id), "inserted": inserted, "updated": updated},
            )

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/introducers", status_code=303)
