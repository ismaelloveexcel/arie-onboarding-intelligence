from __future__ import annotations

import re

CANONICAL_STATUSES: tuple[str, ...] = (
    "new",
    "reviewing",
    "qualified",
    "not_relevant",
    "deferred",
    "contacted",
    "onboarding",
    "not_fit",
)

STATUS_LABELS: dict[str, str] = {
    "new": "New",
    "reviewing": "Reviewing",
    "qualified": "Qualified",
    "not_relevant": "Not Relevant",
    "deferred": "Deferred",
    "contacted": "Contacted",
    "onboarding": "Onboarding",
    "not_fit": "Not Fit",
}

_ALIASES: dict[str, str] = {
    "new": "new",
    "researching": "reviewing",
    "reviewing": "reviewing",
    "outreach_ready": "qualified",
    "qualified": "qualified",
    "not_relevant": "not_relevant",
    "deferred": "deferred",
    "contacted": "contacted",
    "opportunity": "onboarding",
    "client": "onboarding",
    "onboarding": "onboarding",
    "closed_not_fit": "not_fit",
    "not_fit": "not_fit",
}


def _status_key(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    value = value.replace("—", "-").replace("–", "-")
    value = re.sub(r"[\s\-]+", "_", value)
    return value


def normalize_status(raw: str | None) -> str | None:
    key = _status_key(raw)
    return _ALIASES.get(key)


def require_canonical_status(raw: str | None) -> str:
    canonical = normalize_status(raw)
    if canonical is None:
        raise ValueError(f"Unknown status: {raw!r}")
    return canonical


def status_label(canonical: str | None) -> str:
    key = normalize_status(canonical)
    if key is None:
        return STATUS_LABELS["new"]
    return STATUS_LABELS[key]


def status_options() -> list[dict[str, str]]:
    return [{"value": value, "label": STATUS_LABELS[value]} for value in CANONICAL_STATUSES]
