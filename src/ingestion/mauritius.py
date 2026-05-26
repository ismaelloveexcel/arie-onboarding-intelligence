"""
Mauritius CBRD online search scraper.

Source: https://onlinesearch.mns.mu/  (the official MNS portal — the old
mns.govmu.org/Pages/Business-Registration-Database.aspx URL is dead).

Filters by incorporation/registration date range and keeps only GBC and
Authorised Company rows. Ported from the ARIE-global-incorporations-tracker
project where this scraper has been running in production.
"""
import logging
import os
import re
import time
from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

from src.db import upsert_company

logger = logging.getLogger(__name__)

_BASE_URL = "https://onlinesearch.mns.mu/"
_RETRIES = 3
_RETRY_DELAY_SECONDS = 10
_ATTEMPT_TIMEOUT_MS = 90_000
_DEFAULT_LOOKBACK_DAYS = int(os.getenv("MAURITIUS_LOOKBACK_DAYS", "30"))

_HEADER_MAP = {
    "name": "company_name",
    "fileno.": "file_no",
    "fileno": "file_no",
    "category": "entity_type",
    "incorporation/registrationdate": "incorporation_date",
    "nature": "nature",
    "status": "company_status",
}


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def _iso_to_dmy(iso_date: str) -> str:
    return datetime.strptime(iso_date.strip(), "%Y-%m-%d").strftime("%d/%m/%Y")


def _dmy_to_iso(dmy: str) -> str | None:
    text = (dmy or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _category_matches(category: str) -> bool:
    text = (category or "").strip().lower()
    if not text:
        return False
    if "global business company" in text or "gbc" in text:
        return True
    if "authorised company" in text or "authorized company" in text:
        return True
    if re.search(r"\bac\b", text):
        return True
    return False


def _normalise_header(label: str) -> str:
    return label.strip().replace(" ", "").lower()


def _header_to_field(key: str) -> str | None:
    if not key or key.startswith("#"):
        return None
    if key in _HEADER_MAP:
        return _HEADER_MAP[key]
    if "incorporation" in key and "date" in key:
        return "incorporation_date"
    return None


def _column_index_by_header(headers: list[str]) -> dict[str, int]:
    indices: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        field = _header_to_field(_normalise_header(raw))
        if field:
            indices[field] = idx
    return indices


def _accept_cookies_if_present(page) -> None:
    for label in ("Accept All", "Accept all", "Accept"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _input_blob(el) -> str:
    parts = [
        el.get_attribute("id") or "",
        el.get_attribute("placeholder") or "",
        el.get_attribute("formcontrolname") or "",
        el.get_attribute("aria-label") or "",
    ]
    return " ".join(parts).lower()


def _fill_input(el, iso_date: str) -> str:
    input_type = (el.get_attribute("type") or "").lower()
    value = iso_date if input_type == "date" else _iso_to_dmy(iso_date)
    el.fill(value)
    try:
        return el.input_value()
    except Exception:
        return el.get_attribute("value") or value


def _find_date_field(page, kind: str):
    inputs = page.locator("input[type='date'], input[type='text']")
    candidates: list[tuple[int, object]] = []
    for i in range(min(inputs.count(), 24)):
        el = inputs.nth(i)
        try:
            if not el.is_visible():
                continue
            blob = _input_blob(el)
            if "date" not in blob and "from" not in blob and "to" not in blob:
                continue
            is_from = "from" in blob
            is_to = "to" in blob and not is_from
            if kind == "from" and not is_from:
                continue
            if kind == "to" and not is_to:
                continue
            score = 0
            if "incorporation" in blob or "registration" in blob:
                score += 10
            if "partnership" in blob or "company" in blob:
                score -= 2
            candidates.append((score, el))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _fill_date_range(page, date_from: str, date_to: str) -> None:
    from_el = _find_date_field(page, "from")
    to_el = _find_date_field(page, "to")
    if from_el is None or to_el is None:
        raise RuntimeError("Could not locate incorporation date From/To fields")
    _fill_input(from_el, date_from)
    page.wait_for_timeout(500)
    _fill_input(to_el, date_to)
    page.wait_for_timeout(500)


def _click_search(page) -> None:
    for locator in (
        page.get_by_role("button", name=re.compile(r"^search$", re.I)),
        page.locator("button:has-text('Search')"),
        page.locator("input[type='submit'][value*='Search' i]"),
    ):
        try:
            if locator.count():
                locator.first.click(timeout=10_000)
                return
        except Exception:
            continue
    raise RuntimeError("Could not locate Search button")


def _parse_results_table(page) -> list[dict]:
    table = page.locator("table").filter(has=page.locator("th")).first
    table.wait_for(state="visible", timeout=30_000)

    raw_headers = [th.inner_text() for th in table.locator("th").all()]
    col = _column_index_by_header(raw_headers)
    if "company_name" not in col:
        raise RuntimeError(f"Results table missing Name column; headers={raw_headers!r}")

    rows: list[dict] = []
    for tr in table.locator("tbody tr").all():
        cells = tr.locator("td").all()
        if not cells:
            continue
        values = [c.inner_text().strip() for c in cells]
        row: dict = {}
        for field, idx in col.items():
            if idx < len(values):
                row[field] = values[idx]
        if row.get("company_name"):
            rows.append(row)
    return rows


def _has_next_page(page) -> bool:
    for locator in (
        page.get_by_role("link", name=re.compile(r"next", re.I)),
        page.get_by_role("button", name=re.compile(r"next", re.I)),
        page.locator("a:has-text('Next')"),
        page.locator("button:has-text('Next')"),
    ):
        try:
            if not locator.count():
                continue
            el = locator.first
            if not el.is_visible():
                continue
            disabled = el.get_attribute("disabled")
            aria_disabled = el.get_attribute("aria-disabled")
            classes = el.get_attribute("class") or ""
            if (
                disabled is not None
                or aria_disabled == "true"
                or "disabled" in classes.lower()
            ):
                continue
            return True
        except Exception:
            continue
    return False


def _click_next_page(page) -> None:
    for locator in (
        page.get_by_role("link", name=re.compile(r"next", re.I)),
        page.get_by_role("button", name=re.compile(r"next", re.I)),
        page.locator("a:has-text('Next')"),
        page.locator("button:has-text('Next')"),
    ):
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=10_000)
                page.wait_for_load_state("networkidle", timeout=60_000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue
    raise RuntimeError("Next page control not clickable")


def _scrape_date_range(page, date_from: str, date_to: str) -> list[dict]:
    page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    _accept_cookies_if_present(page)
    _fill_date_range(page, date_from, date_to)
    _click_search(page)
    page.wait_for_load_state("networkidle", timeout=60_000)
    page.wait_for_timeout(2000)

    out: list[dict] = []
    seen: set[str] = set()
    page_num = 1
    while True:
        try:
            raw_rows = _parse_results_table(page)
        except Exception as exc:
            if page_num == 1:
                raise
            logger.warning(
                "mauritius_pagination_parse_failed",
                extra={"page": page_num, "error": str(exc)},
            )
            break
        for row in raw_rows:
            name = (row.get("company_name") or "").strip()
            file_no = (row.get("file_no") or "").strip()
            key = f"{name}|{file_no}"
            if not name or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "company_name": name,
                    "registration_number": file_no,
                    "entity_type": (row.get("entity_type") or "").strip(),
                    "incorporation_date": _dmy_to_iso(row.get("incorporation_date", "")),
                    "nature": (row.get("nature") or "").strip(),
                    "company_status": (row.get("company_status") or "").strip(),
                }
            )
        if not _has_next_page(page):
            break
        try:
            _click_next_page(page)
        except Exception as exc:
            logger.warning("mauritius_pagination_stopped", extra={"error": str(exc)})
            break
        page_num += 1
    return out


def _scrape_mns(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    Playwright scraper for the Mauritius CBRD online search portal.

    Returns raw row dicts (pre-filter) so the caller applies the GBC /
    Authorised Company filter and persistence logic.
    """
    from playwright.sync_api import sync_playwright

    end_iso = date_to or date.today().isoformat()
    start_iso = date_from or (
        date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    ).isoformat()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            )
            page = context.new_page()
            page.set_default_timeout(_ATTEMPT_TIMEOUT_MS)
            return _scrape_date_range(page, start_iso, end_iso)
        finally:
            browser.close()


def _upsert_item(conn, item: dict) -> None:
    name = item.get("company_name") or ""
    entity_type = item.get("entity_type") or ""
    upsert_company(
        conn,
        {
            "source_system": "mauritius_mns",
            "source_ref": item.get("registration_number") or name,
            "company_name": name,
            "normalised_name": _normalise(name),
            "jurisdiction": "Mauritius",
            "entity_type": entity_type,
            "incorporation_date": item.get("incorporation_date"),
            "registered_address": None,
            "sic_codes": [],
            "website": None,
            "verify_url": _BASE_URL,
            "raw_data": Jsonb(item),
        },
    )


def fetch_mauritius_incorporations(conn) -> int:
    """
    Scrape Mauritius MNS for recent GBC and Authorised Company incorporations.
    Returns count of records processed. Failure does NOT raise — caller
    receives 0 and a WARNING is logged.
    """
    for attempt in range(1, _RETRIES + 1):
        try:
            companies = _scrape_mns()
            if not companies:
                logger.warning("mauritius_scraper_zero_results", extra={"attempt": attempt})
                return 0

            count = 0
            for company in companies:
                if not _category_matches(company.get("entity_type") or ""):
                    continue
                _upsert_item(conn, company)
                count += 1

            conn.commit()
            logger.info(
                "mauritius_fetch_done",
                extra={"count": count, "total_rows": len(companies)},
            )
            return count

        except Exception as exc:
            logger.warning(
                "mauritius_scraper_error",
                extra={"attempt": attempt, "error": str(exc)},
            )
            if attempt < _RETRIES:
                time.sleep(_RETRY_DELAY_SECONDS)

    logger.error("mauritius_scraper_all_retries_failed")
    return 0
