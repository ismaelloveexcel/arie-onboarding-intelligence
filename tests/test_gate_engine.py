from __future__ import annotations

from pathlib import Path

import yaml

from scripts.pilot_gates.gate_engine import CISuiteResult, validate_gate_state


def _base_state() -> dict:
    return {
        "updated_at": "2026-06-05",
        "branch_baseline": "cursor/pilot-readiness-gates-3f07",
        "gates": {
            "A": {"name": "Status Integrity", "status": "complete"},
            "B": {"name": "Write Authorization", "status": "complete"},
            "C": {"name": "Deterministic Scoring", "status": "complete"},
            "D": {"name": "URL Safety", "status": "complete"},
            "E": {"name": "LEI Matching Safety", "status": "complete"},
            "F": {"name": "Mutation Isolation", "status": "complete"},
        },
        "validation_snapshot": {
            "local_pilot_gate_tests_passed": 31,
            "checks": [],
        },
        "transition_state": {
            "stabilization_complete": False,
            "pilot_gates_ci_green": True,
            "open_incidents_p0_p1": 0,
            "phase_a_pass": False,
            "manus_phase_a_allowed": False,
            "manus_phase_b_allowed": False,
        },
        "stabilization_evidence": {
            "report_path": "pending",
            "commit_hash": "pending",
            "ci_run_id": "",
            "metrics_snapshot_hash": "pending",
        },
    }


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(state, sort_keys=False))


def test_gate_engine_accepts_consistent_pre_stabilization_state(tmp_path: Path):
    state_path = tmp_path / "docs" / "current-gate-status.yaml"
    _write_state(state_path, _base_state())

    result = validate_gate_state(
        state_path=state_path,
        repo_root=tmp_path,
    )

    assert result.is_valid
    assert result.computed_state["stabilization_eligible"] is False
    assert result.computed_state["manus_phase_a_allowed"] is False
    assert result.computed_state["manus_phase_b_allowed"] is False


def test_gate_engine_rejects_illegal_manus_phase_a_flag(tmp_path: Path):
    state = _base_state()
    state["transition_state"]["manus_phase_a_allowed"] = True
    state_path = tmp_path / "docs" / "current-gate-status.yaml"
    _write_state(state_path, state)

    result = validate_gate_state(
        state_path=state_path,
        repo_root=tmp_path,
    )

    assert not result.is_valid
    assert any("manus_phase_a_allowed cannot be true" in err for err in result.errors)


def test_gate_engine_requires_eligibility_before_stabilization_complete(tmp_path: Path):
    state = _base_state()
    state["transition_state"]["stabilization_complete"] = True
    state_path = tmp_path / "docs" / "current-gate-status.yaml"
    _write_state(state_path, state)

    result = validate_gate_state(
        state_path=state_path,
        repo_root=tmp_path,
    )

    assert not result.is_valid
    assert any(
        "stabilization_complete cannot be true while stabilization_eligible is false"
        in err
        for err in result.errors
    )


def test_gate_engine_computes_stabilization_eligibility_when_evidence_is_present(
    tmp_path: Path,
):
    state = _base_state()
    report = tmp_path / "docs" / "stabilization-report-2026-06-05.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# stabilization report")
    state["stabilization_evidence"] = {
        "report_path": "docs/stabilization-report-2026-06-05.md",
        "commit_hash": "abc1234",
        "ci_run_id": "12345",
        "metrics_snapshot_hash": "deadbeef",
    }
    state_path = tmp_path / "docs" / "current-gate-status.yaml"
    _write_state(state_path, state)

    result = validate_gate_state(
        state_path=state_path,
        repo_root=tmp_path,
        ci_mode=True,
        ci_suite_result=CISuiteResult(
            deterministic_tests_passing=True,
            command_failures=[],
        ),
    )

    assert result.is_valid
    assert result.computed_state["stabilization_eligible"] is True


def test_gate_engine_rejects_computed_state_field_in_yaml(tmp_path: Path):
    state = _base_state()
    state["transition_state"]["stabilization_eligible"] = False
    state_path = tmp_path / "docs" / "current-gate-status.yaml"
    _write_state(state_path, state)

    result = validate_gate_state(
        state_path=state_path,
        repo_root=tmp_path,
    )

    assert not result.is_valid
    assert any(
        "must not be stored in YAML" in err
        for err in result.errors
    )
