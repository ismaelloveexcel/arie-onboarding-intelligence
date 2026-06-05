from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Hard rule:
# YAML is declarative state.
# Gate engine is authoritative computed state.
# No external input may override computed truth.
REQUIRED_GATE_IDS = ("A", "B", "C", "D", "E", "F")
DETERMINISTIC_TEST_FILES = (
    "tests/test_shadow_scoring.py",
    "tests/test_snapshot_determinism.py",
    "tests/test_scoring_parity.py",
    "tests/test_score_runs_audit.py",
    "tests/test_backfill_guardrails.py",
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{7,64}$", flags=re.IGNORECASE)
PLACEHOLDER_VALUE = "pending"


@dataclass
class CISuiteResult:
    deterministic_tests_passing: bool
    command_failures: list[str] = field(default_factory=list)


@dataclass
class GateEngineResult:
    computed_state: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    command_failures: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.command_failures


def _expect_mapping(
    value: Any, label: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be a mapping")
        return None
    return value


def _expect_bool(
    mapping: dict[str, Any], key: str, label: str, errors: list[str]
) -> bool | None:
    value = mapping.get(key)
    if not isinstance(value, bool):
        errors.append(f"{label}.{key} must be a boolean")
        return None
    return value


def _optional_bool(
    mapping: dict[str, Any], key: str, label: str, errors: list[str]
) -> bool | None:
    if key not in mapping:
        return None
    return _expect_bool(mapping, key, label, errors)


def _expect_int(
    mapping: dict[str, Any], key: str, label: str, errors: list[str]
) -> int | None:
    value = mapping.get(key)
    if not isinstance(value, int):
        errors.append(f"{label}.{key} must be an integer")
        return None
    return value


def _expect_text(
    mapping: dict[str, Any], key: str, label: str, errors: list[str], *, allow_empty: bool
) -> str | None:
    value = mapping.get(key)
    if not isinstance(value, str):
        errors.append(f"{label}.{key} must be a string")
        return None
    if not allow_empty and value.strip() == "":
        errors.append(f"{label}.{key} must not be empty")
        return None
    return value.strip()


def _is_placeholder(value: str) -> bool:
    return value.lower() == PLACEHOLDER_VALUE


def _resolve_path(repo_root: Path, path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else repo_root / path


def _evidence_exists(
    evidence: dict[str, Any], repo_root: Path, errors: list[str]
) -> bool:
    report_path_raw = _expect_text(
        evidence,
        "report_path",
        "stabilization_evidence",
        errors,
        allow_empty=False,
    )
    commit_hash_raw = _expect_text(
        evidence,
        "commit_hash",
        "stabilization_evidence",
        errors,
        allow_empty=False,
    )
    _expect_text(
        evidence,
        "ci_run_id",
        "stabilization_evidence",
        errors,
        allow_empty=True,
    )
    metrics_hash_raw = _expect_text(
        evidence,
        "metrics_snapshot_hash",
        "stabilization_evidence",
        errors,
        allow_empty=False,
    )
    if (
        report_path_raw is None
        or commit_hash_raw is None
        or metrics_hash_raw is None
    ):
        return False

    report_path = report_path_raw.strip()
    commit_hash = commit_hash_raw.strip()
    metrics_hash = metrics_hash_raw.strip()

    if _is_placeholder(report_path) or _is_placeholder(commit_hash) or _is_placeholder(
        metrics_hash
    ):
        return False

    if not HASH_PATTERN.fullmatch(commit_hash):
        errors.append("stabilization_evidence.commit_hash must look like a git hash")
        return False

    if not HASH_PATTERN.fullmatch(metrics_hash):
        errors.append(
            "stabilization_evidence.metrics_snapshot_hash must be a stable hash"
        )
        return False

    if not _resolve_path(repo_root, report_path).exists():
        errors.append(
            "stabilization_evidence.report_path must reference an existing file"
        )
        return False

    return True


def _all_gates_complete(gates: dict[str, Any], errors: list[str]) -> bool:
    complete = True
    for gate_id in REQUIRED_GATE_IDS:
        gate_node = _expect_mapping(gates.get(gate_id), f"gates.{gate_id}", errors)
        if gate_node is None:
            complete = False
            continue
        status = gate_node.get("status")
        if not isinstance(status, str):
            errors.append(f"gates.{gate_id}.status must be a string")
            complete = False
            continue
        if status.strip().lower() != "complete":
            complete = False
    return complete


def _command_failure_message(name: str, command: list[str], output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    tail = "\n".join(lines[-20:]) if lines else "(no command output captured)"
    return (
        f"{name} failed: {' '.join(command)}\n"
        f"--- output tail ---\n{tail}\n--- end output tail ---"
    )


def _run_ci_suite(repo_root: Path) -> CISuiteResult:
    command_failures: list[str] = []
    deterministic_tests_passing = False
    python = sys.executable
    deterministic_command = [python, "-m", "pytest", "-q", *DETERMINISTIC_TEST_FILES]
    ci_commands: list[tuple[str, list[str]]] = [
        ("ruff", [python, "-m", "ruff", "check", "src", "tests", "scripts"]),
        ("compile", [python, "-m", "compileall", "src", "tests", "scripts"]),
        ("gate-a", [python, "-m", "pytest", "-q", "tests/test_status_integrity.py"]),
        ("gate-b", [python, "-m", "pytest", "-q", "tests/test_write_guard.py"]),
        ("gate-d", [python, "-m", "pytest", "-q", "tests/test_url_safety.py"]),
        ("gate-c-deterministic", deterministic_command),
        ("gate-e", [python, "-m", "pytest", "-q", "tests/test_lei_matching_safety.py"]),
        ("gate-f", [python, "-m", "pytest", "-q", "tests/test_mutation_isolation.py"]),
    ]

    for name, command in ci_commands:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        combined_output = f"{completed.stdout}\n{completed.stderr}"
        if name == "gate-c-deterministic":
            deterministic_tests_passing = completed.returncode == 0
        if completed.returncode != 0:
            command_failures.append(
                _command_failure_message(name, command, combined_output)
            )

    return CISuiteResult(
        deterministic_tests_passing=deterministic_tests_passing,
        command_failures=command_failures,
    )


def validate_gate_state(
    *,
    state_path: Path,
    repo_root: Path,
    ci_mode: bool = False,
    ci_suite_result: CISuiteResult | None = None,
) -> GateEngineResult:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(state_path.read_text()) or {}
    except FileNotFoundError:
        return GateEngineResult(
            computed_state={},
            errors=[f"State file not found: {state_path}"],
        )
    except yaml.YAMLError as exc:
        return GateEngineResult(
            computed_state={},
            errors=[f"Invalid YAML in {state_path}: {exc}"],
        )

    root = _expect_mapping(raw, "root", errors)
    if root is None:
        return GateEngineResult(computed_state={}, errors=errors)

    gates = _expect_mapping(root.get("gates"), "gates", errors) or {}
    transition = (
        _expect_mapping(root.get("transition_state"), "transition_state", errors) or {}
    )
    validation_snapshot = (
        _expect_mapping(root.get("validation_snapshot"), "validation_snapshot", errors)
        or {}
    )
    evidence = (
        _expect_mapping(root.get("stabilization_evidence"), "stabilization_evidence", errors)
        or {}
    )

    if "stabilization_eligible" in transition:
        errors.append(
            "transition_state.stabilization_eligible is computed and must not be stored in YAML"
        )

    pilot_gates_ci_green = _expect_bool(
        transition, "pilot_gates_ci_green", "transition_state", errors
    )
    stabilization_complete = _expect_bool(
        transition, "stabilization_complete", "transition_state", errors
    )
    phase_a_pass = _expect_bool(transition, "phase_a_pass", "transition_state", errors)
    open_incidents = _expect_int(
        transition, "open_incidents_p0_p1", "transition_state", errors
    )
    yaml_phase_a_allowed = _expect_bool(
        transition, "manus_phase_a_allowed", "transition_state", errors
    )
    yaml_phase_b_allowed = _expect_bool(
        transition, "manus_phase_b_allowed", "transition_state", errors
    )

    declared_deterministic = _optional_bool(
        validation_snapshot,
        "deterministic_tests_passing",
        "validation_snapshot",
        errors,
    )

    command_failures: list[str] = []
    if ci_mode:
        ci_result = ci_suite_result or _run_ci_suite(repo_root)
        deterministic_tests_passing = ci_result.deterministic_tests_passing
        command_failures = ci_result.command_failures
    else:
        deterministic_tests_passing = (
            declared_deterministic if declared_deterministic is not None else False
        )

    all_gates_complete = _all_gates_complete(gates, errors)
    evidence_exists = _evidence_exists(evidence, repo_root, errors)
    stabilization_eligible = (
        pilot_gates_ci_green is True
        and open_incidents == 0
        and all_gates_complete
        and deterministic_tests_passing is True
        and evidence_exists
    )
    computed_phase_a_allowed = stabilization_complete is True and phase_a_pass is False
    computed_phase_b_allowed = phase_a_pass is True

    computed_state = {
        "all_gates_complete": all_gates_complete,
        "deterministic_tests_passing": deterministic_tests_passing,
        "evidence_exists": evidence_exists,
        "stabilization_eligible": stabilization_eligible,
        "manus_phase_a_allowed": computed_phase_a_allowed,
        "manus_phase_b_allowed": computed_phase_b_allowed,
    }

    if yaml_phase_a_allowed is True and stabilization_complete is not True:
        errors.append(
            "Illegal state: manus_phase_a_allowed cannot be true when stabilization_complete is false"
        )
    if yaml_phase_b_allowed is True and phase_a_pass is not True:
        errors.append(
            "Illegal state: manus_phase_b_allowed cannot be true when phase_a_pass is false"
        )
    if phase_a_pass is True and stabilization_complete is not True:
        errors.append(
            "Illegal state: phase_a_pass cannot be true when stabilization_complete is false"
        )

    if yaml_phase_a_allowed is not None and yaml_phase_a_allowed != computed_phase_a_allowed:
        errors.append(
            "Flag mismatch: transition_state.manus_phase_a_allowed does not match computed eligibility"
        )
    if yaml_phase_b_allowed is not None and yaml_phase_b_allowed != computed_phase_b_allowed:
        errors.append(
            "Flag mismatch: transition_state.manus_phase_b_allowed does not match computed eligibility"
        )
    if stabilization_complete is True and stabilization_eligible is not True:
        errors.append(
            "Illegal state: stabilization_complete cannot be true while stabilization_eligible is false"
        )

    return GateEngineResult(
        computed_state=computed_state,
        errors=errors,
        command_failures=command_failures,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-pass governance enforcement gate engine."
    )
    parser.add_argument(
        "--state-file",
        default="docs/current-gate-status.yaml",
        help="Path to the canonical state YAML file.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Run CI mode (gate engine executes enforcement suite internally).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    state_path = repo_root / args.state_file
    result = validate_gate_state(
        state_path=state_path,
        repo_root=repo_root,
        ci_mode=args.ci,
    )

    print("Computed state:")
    print(json.dumps(result.computed_state, indent=2, sort_keys=True))

    if result.errors:
        print("\nValidation failures:")
        for failure in result.errors:
            print(f" - {failure}")

    if result.command_failures:
        print("\nEnforcement command failures:")
        for failure in result.command_failures:
            print(f" - {failure}")

    if result.is_valid:
        print("\nValidation result: PASS")
        return 0

    print("\nValidation result: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
