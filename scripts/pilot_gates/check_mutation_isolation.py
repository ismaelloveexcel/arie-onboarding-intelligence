from __future__ import annotations

import ast
import re
from pathlib import Path

ALLOWED_WRITE_PATHS = {
    "src/db.py",
    "src/main.py",
    "src/introducers.py",
    "src/pipeline.py",
    "src/shadow_scoring.py",
    "src/ingestion/companies_house.py",
    "src/ingestion/gleif.py",
    "src/ingestion/lei_backfill.py",
    "src/ingestion/mauritius.py",
}

PROTECTED_TABLES = (
    "companies",
    "rm_actions",
    "audit_log",
    "lead_contacts",
    "introducers",
    "introducer_actions",
    "pending_uploads",
    "lei_records",
    "lead_signal_scores",
    "lead_score_evidence",
    "score_runs",
    "score_versions",
    "company_enrichment",
    "normalized_company_metrics",
    "company_contacts",
    "decision_maker_contacts",
    "director_profiles",
    "director_contact_candidates",
    "director_social_profiles",
)

WRITE_SQL_PATTERN = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", flags=re.IGNORECASE
)
TABLE_PATTERN = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
    flags=re.IGNORECASE,
)


def _extract_sql_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    sqls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "execute":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            sqls.append((node.lineno, first.value))
        elif isinstance(first, ast.JoinedStr):
            # Conservative fallback for f-strings.
            literal = "".join(
                part.value for part in first.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if literal:
                sqls.append((node.lineno, literal))
    return sqls


def find_mutation_isolation_violations(repo_root: Path) -> list[str]:
    violations: list[str] = []
    src_root = repo_root / "src"
    for path in src_root.rglob("*.py"):
        rel = path.relative_to(repo_root).as_posix()
        sql_literals = _extract_sql_literals(path)
        for lineno, sql in sql_literals:
            if not WRITE_SQL_PATTERN.search(sql):
                continue
            table_matches = {
                match.group(1).lower()
                for match in TABLE_PATTERN.finditer(sql)
            }
            protected_touched = table_matches.intersection(PROTECTED_TABLES)
            if not protected_touched:
                continue
            if rel not in ALLOWED_WRITE_PATHS:
                violations.append(
                    f"{rel}:{lineno} writes protected tables {sorted(protected_touched)}"
                )
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    violations = find_mutation_isolation_violations(repo_root)
    if violations:
        print("Mutation isolation violations found:")
        for item in violations:
            print(f" - {item}")
        return 1
    print("Mutation isolation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
