#!/usr/bin/env python3
"""Bootstrap and preflight checks for the cloud working portal.

Usage examples:
  python3 scripts/portal_bootstrap.py --install-railway
  python3 scripts/portal_bootstrap.py --mode deploy --install-railway
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

APP_REQUIRED_ENV = (
    "DATABASE_URL",
    "COMPANIES_HOUSE_API_KEY",
    "SECRET_KEY",
    "RM_NAMES",
    "ACTOR_NAMES",
    "APP_ENV",
)

DEPLOY_REQUIRED_ENV = ("RAILWAY_TOKEN",)


def _run(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip()


def _ensure_railway_path() -> None:
    railway_bin = str(Path.home() / ".railway" / "bin")
    current = os.environ.get("PATH", "")
    if railway_bin not in current.split(":"):
        os.environ["PATH"] = f"{railway_bin}:{current}" if current else railway_bin


def missing_env_vars(
    required_vars: Iterable[str],
    environ: dict[str, str] | None = None,
) -> list[str]:
    source = environ if environ is not None else os.environ
    return [key for key in required_vars if not source.get(key, "").strip()]


def install_railway_cli() -> bool:
    if shutil.which("railway"):
        return True

    system = platform.system().lower()
    if system not in {"linux", "darwin"}:
        print("Railway CLI auto-install is only supported on Linux/macOS.")
        return False

    installer = subprocess.run(
        "curl -fsSL https://railway.com/install.sh | sh",
        shell=True,
        executable="/bin/bash",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _ensure_railway_path()
    if installer.returncode != 0:
        print("Railway install failed:")
        print(installer.stdout.strip())
        return False
    return shutil.which("railway") is not None


def check_railway_cli() -> bool:
    _ensure_railway_path()
    if not shutil.which("railway"):
        print("Railway CLI missing. Run with --install-railway.")
        return False
    code, output = _run(["railway", "--version"])
    if code != 0:
        print("Railway CLI exists but version check failed:")
        print(output)
        return False
    print(output)
    return True


def check_railway_auth() -> bool:
    token = os.getenv("RAILWAY_TOKEN", "").strip() or os.getenv(
        "RAILWAY_API_TOKEN", ""
    ).strip()
    if not token:
        print("Missing Railway token: set RAILWAY_TOKEN (or RAILWAY_API_TOKEN).")
        return False
    code, output = _run(["railway", "whoami"])
    if code != 0:
        print("Railway auth check failed:")
        print(output)
        return False
    print(output)
    return True


def persist_railway_path() -> None:
    line = 'export PATH="$HOME/.railway/bin:$PATH"\n'
    for rc_file in (Path.home() / ".bashrc", Path.home() / ".profile"):
        current = rc_file.read_text() if rc_file.exists() else ""
        if line.strip() in current:
            continue
        updated = current + ("\n" if current and not current.endswith("\n") else "")
        updated += line
        rc_file.write_text(updated)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("app", "deploy", "all"),
        default="all",
        help="app: runtime vars, deploy: Railway auth/token, all: both",
    )
    parser.add_argument(
        "--install-railway",
        action="store_true",
        help="Install Railway CLI if missing (Linux/macOS).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ok = True

    if args.install_railway:
        if not install_railway_cli():
            ok = False
        else:
            persist_railway_path()

    if args.mode in {"deploy", "all"}:
        ok = check_railway_cli() and ok
        ok = check_railway_auth() and ok
        missing = missing_env_vars(DEPLOY_REQUIRED_ENV)
        if missing:
            print(f"Missing deploy env vars: {', '.join(missing)}")
            ok = False

    if args.mode in {"app", "all"}:
        missing = missing_env_vars(APP_REQUIRED_ENV)
        if missing:
            print(f"Missing app env vars: {', '.join(missing)}")
            ok = False
        else:
            print("App env vars: OK")

    if not ok:
        print(
            "Preflight failed. Add missing secrets in your cloud environment, then rerun."
        )
        return 1

    print("Portal bootstrap/preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
