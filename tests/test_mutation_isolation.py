from pathlib import Path

from scripts.pilot_gates.check_mutation_isolation import find_mutation_isolation_violations


def test_mutation_isolation_static_scan_passes():
    repo_root = Path(__file__).resolve().parents[1]
    violations = find_mutation_isolation_violations(repo_root)
    assert violations == []
