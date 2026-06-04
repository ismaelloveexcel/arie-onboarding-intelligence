from fastapi.testclient import TestClient

from src.db import get_conn, upsert_company
from src.main import app
from src.scoring import calculate_score


def test_route_root_returns_200():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200


def test_uk_holding_company_scores_at_least_70():
    score, _, _ = calculate_score(
        {
            "company_name": "Global Capital Holdings Ltd",
            "jurisdiction": "UK",
            "entity_type": "holding company",
            "sic_codes": ["64200"],
        }
    )
    assert score >= 70


def test_audit_log_write_on_rm_action():
    client = TestClient(app)
    company_ref = "TEST_AUDIT_LOG_WRITE"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM audit_log WHERE entity_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute(
                "DELETE FROM rm_actions WHERE company_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute(
                "DELETE FROM lead_scores WHERE company_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute("DELETE FROM companies WHERE source_ref = %s", (company_ref,))
        conn.commit()

        company_id = upsert_company(
            conn,
            {
                "source_system": "companies_house",
                "source_ref": company_ref,
                "company_name": "Audit Log Test Ltd",
                "normalised_name": "audit log test ltd",
                "jurisdiction": "UK",
                "entity_type": "private limited company",
                "incorporation_date": None,
                "registered_address": None,
                "sic_codes": [],
                "website": None,
                "verify_url": None,
                "raw_data": "{}",
            },
        )
        conn.commit()

    response = client.post(
        f"/leads/{company_id}/action",
        data={"assigned_to": "Test", "status": "Reviewing", "notes": "Checked"},
    )
    assert response.status_code == 200

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_log WHERE entity_id = %s", (company_id,)
            )
            audit_count = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM rm_actions WHERE company_id = %s", (company_id,)
            )
            action_count = cur.fetchone()[0]
            cur.execute("DELETE FROM audit_log WHERE entity_id = %s", (company_id,))
            cur.execute("DELETE FROM rm_actions WHERE company_id = %s", (company_id,))
            cur.execute("DELETE FROM lead_scores WHERE company_id = %s", (company_id,))
            cur.execute("DELETE FROM companies WHERE id = %s", (company_id,))
        conn.commit()

    assert audit_count >= 1
    assert action_count == 1


def test_lead_detail_page_renders_for_existing_company():
    client = TestClient(app)
    company_ref = "TEST_LEAD_DETAIL_RENDER"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM audit_log WHERE entity_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute(
                "DELETE FROM rm_actions WHERE company_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute(
                "DELETE FROM lead_scores WHERE company_id IN (SELECT id FROM companies WHERE source_ref = %s)",
                (company_ref,),
            )
            cur.execute("DELETE FROM companies WHERE source_ref = %s", (company_ref,))
        conn.commit()

        company_id = upsert_company(
            conn,
            {
                "source_system": "companies_house",
                "source_ref": company_ref,
                "company_name": "Lead Detail Render Test Ltd",
                "normalised_name": "lead detail render test ltd",
                "jurisdiction": "UK",
                "entity_type": "private limited company",
                "incorporation_date": None,
                "registered_address": None,
                "sic_codes": [],
                "website": None,
                "verify_url": None,
                "raw_data": "{}",
            },
        )
        conn.commit()

    response = client.get(f"/leads/{company_id}")
    assert response.status_code == 200
    assert "Lead Detail Render Test Ltd" in response.text
    assert "Directors" in response.text
    assert "Owners / UBOs" in response.text
    assert "Contacts" not in response.text

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_log WHERE entity_id = %s", (company_id,))
            cur.execute("DELETE FROM rm_actions WHERE company_id = %s", (company_id,))
            cur.execute("DELETE FROM lead_scores WHERE company_id = %s", (company_id,))
            cur.execute("DELETE FROM companies WHERE id = %s", (company_id,))
        conn.commit()
