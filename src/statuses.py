"""Shared status definitions and compatibility normalization.

Phase 1 compatibility layer:
- Accept legacy/status-label variants at the app boundary.
- Persist canonical values internally to avoid DB drift.
"""

from __future__ import annotations

CANONICAL_STATUSES: tuple[str, ...] = (
    "New",
    "Reviewing",
    "Qualified",
    "Not Relevant",
    "Deferred",
    "Contacted",
    "Onboarding",
    "Not Fit",
)

# Temporary aliases for compatibility with already-shared UI terminology.
# This map should be time-boxed and removed after status cleanup migration.
STATUS_ALIASES: dict[str, str] = {
    "researching": "Reviewing",
    "outreach ready": "Qualified",
    "opportunity": "Onboarding",
    "client": "Onboarding",
    "closed — not fit": "Not Fit",
    "closed - not fit": "Not Fit",
}


def normalize_status(raw: str | None) -> str:
    """Convert a raw status string to canonical status."""
    text = (raw or "").strip()
    if not text:
        return "New"
    canonical_lookup = {value.lower(): value for value in CANONICAL_STATUSES}
    if text.lower() in canonical_lookup:
        return canonical_lookup[text.lower()]
    return STATUS_ALIASES.get(text.lower(), text)


def is_known_status(raw: str | None) -> bool:
    """Return True if status is canonical or known compatibility alias."""
    text = (raw or "").strip().lower()
    if not text:
        return True
    return text in {s.lower() for s in CANONICAL_STATUSES} or text in STATUS_ALIASES


def db_compat_statuses() -> tuple[str, ...]:
    """Statuses accepted by DB during compatibility window."""
    aliases = tuple(
        alias for alias in STATUS_ALIASES if alias not in {s.lower() for s in CANONICAL_STATUSES}
    )
    return CANONICAL_STATUSES + tuple(sorted({alias.title() for alias in aliases}))
