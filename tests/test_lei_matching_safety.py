from src.ingestion.gleif import (
    LEI_MATCH_CONFIDENCE_THRESHOLD,
    _find_company_id,
    resolve_company_match,
)


def test_resolve_company_match_verified_on_unique_registered_as(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_source_ref",
        lambda *_args, **_kwargs: ["company-1"],
    )
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_name",
        lambda *_args, **_kwargs: [],
    )

    match = resolve_company_match(object(), "12345678", "Example Ltd")
    assert match.match_state == "VERIFIED"
    assert match.company_id == "company-1"
    assert match.confidence_score == 1.0
    assert match.match_basis == "registered_as"


def test_resolve_company_match_marks_ambiguous_when_multiple_source_ref_hits(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_source_ref",
        lambda *_args, **_kwargs: ["company-1", "company-2"],
    )
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_name",
        lambda *_args, **_kwargs: ["company-3"],
    )

    match = resolve_company_match(object(), "12345678", "Example Ltd")
    assert match.match_state == "AMBIGUOUS"
    assert match.company_id is None
    assert match.confidence_score < LEI_MATCH_CONFIDENCE_THRESHOLD
    assert match.match_basis == "registered_as"


def test_resolve_company_match_falls_back_to_unique_name(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_source_ref",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "src.ingestion.gleif._candidate_ids_by_name",
        lambda *_args, **_kwargs: ["company-9"],
    )

    match = resolve_company_match(object(), None, "Example Ltd")
    assert match.match_state == "VERIFIED"
    assert match.company_id == "company-9"
    assert match.confidence_score >= LEI_MATCH_CONFIDENCE_THRESHOLD
    assert match.match_basis == "normalised_name"


def test_find_company_id_returns_none_for_ambiguous(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.gleif.resolve_company_match",
        lambda *_args, **_kwargs: type(
            "Match",
            (),
            {
                "match_state": "AMBIGUOUS",
                "company_id": None,
                "confidence_score": 0.4,
            },
        )(),
    )
    assert _find_company_id(object(), "12345678", "Example Ltd") is None
