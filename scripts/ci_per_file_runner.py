#!/usr/bin/env python3
"""Run each pytest file in a fresh DB so native index crashes stay attributable."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_per_file_runner.py TEST_ROOT [pytest args...]", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    args = sys.argv[2:]
    files = sorted(root.glob("test_*.py"))
    if not files:
        print(f"no test files found under {root}", file=sys.stderr)
        return 2
    base_db = Path(os.environ.get("MNELO_CI_DB_ROOT", tempfile.mkdtemp(prefix="mnelo-ci-db-")))
    totals = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "crashed": 0}
    failures: list[str] = []
    for test_file in files:
        db = base_db / test_file.stem
        shutil.rmtree(db, ignore_errors=True)
        db.mkdir(parents=True)
        env = os.environ.copy()
        env["MNELO_MEMORY_DIR"] = str(db)
        print(f"===== {test_file} =====", flush=True)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), *args],
            env=env,
            text=True,
        )
        if proc.returncode in (134, 139, -6, -11):
            totals["crashed"] += 1
            failures.append(f"{test_file}: native crash (exit {proc.returncode})")
            continue
        if proc.returncode:
            failures.append(f"{test_file}: pytest exit {proc.returncode}")
        # pytest summary is intentionally parsed from the child output only when
        # available; a crash has already been classified above.
    print("====== CI AGGREGATE ======")
    print(" ".join(f"{k}={v}" for k, v in totals.items()))
    if failures:
        print("-- failures/crashes --")
        print("\n".join(failures))
    # Any ordinary pytest failure is blocking. Native crashes are reported as
    # failures too: CI must not paint a red test suite green.
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
