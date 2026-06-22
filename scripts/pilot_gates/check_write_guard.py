from __future__ import annotations

import ast
from pathlib import Path

MUTATION_VERBS = ("post", "patch", "put", "delete", "assign", "action", "upload", "contact")
MUTATION_HTTP_METHODS = {"post", "patch", "put", "delete"}


def _is_mutation_route_decorator(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr.lower() in MUTATION_HTTP_METHODS
    return False


def _is_write_guard_decorator(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "write_guard_required"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id == "write_guard_required"
    return False


def check_paths(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            lower_name = node.name.lower()
            if not any(verb in lower_name for verb in MUTATION_VERBS):
                continue
            has_mutation_route = any(
                _is_mutation_route_decorator(dec) for dec in node.decorator_list
            )
            if not has_mutation_route:
                continue
            has_guard = any(
                _is_write_guard_decorator(dec) for dec in node.decorator_list
            )
            if not has_guard:
                failures.append(f"{path}:{node.lineno} {node.name}")
    return failures


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    targets = [
        repo_root / "src" / "main.py",
        repo_root / "src" / "introducers.py",
    ]
    failures = check_paths(targets)
    if failures:
        print("Missing @write_guard_required on mutation handlers:")
        for item in failures:
            print(f" - {item}")
        return 1
    print("Write guard AST check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
