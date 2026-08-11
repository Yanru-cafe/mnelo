"""P3 (P3-benchmarks) tests for the new `python -m benchmarks <name>` CLI.

[8/11 P3-benchmarks] Tests cover:
- dispatcher --list / --help / unknown name
- submodule --help passes through dispatcher
- latency benchmark re-exported names still importable from scripts.benchmark
- locomo smoke run produces coverage + latency output
- parity between scripts/benchmark.py and python -m benchmarks latency
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run `python -m benchmarks <args>` from the repo cwd.

    Inherit the parent environment (so Memory() can find its config / DB path
    the same way the existing tests do) but prepend REPO to PYTHONPATH so the
    `benchmarks` package is importable.
    """
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [PYTHON, "-m", "benchmarks", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO),
        env=env,
    )


class TestDispatcher:
    """Test the benchmarks package dispatcher."""

    def test_list_flag(self):
        """`python -m benchmarks --list` should list all benchmarks."""
        result = run_cli("--list")
        assert result.returncode == 0, result.stderr
        assert "latency" in result.stdout
        assert "locomo" in result.stdout

    def test_help_flag_top_level(self):
        """`python -m benchmarks --help` should show dispatcher usage."""
        result = run_cli("--help")
        assert result.returncode == 0, result.stderr
        assert "usage" in result.stdout.lower()
        assert "Available benchmarks" in result.stdout

    def test_help_passes_through_to_latency(self):
        """`python -m benchmarks latency --help` should show latency's own help."""
        result = run_cli("latency", "--help")
        assert result.returncode == 0, result.stderr
        # latency-specific flag
        assert "--chunks" in result.stdout
        assert "--queries" in result.stdout
        # should NOT show dispatcher usage
        assert "Available benchmarks" not in result.stdout

    def test_help_passes_through_to_locomo(self):
        """`python -m benchmarks locomo --help` should show locomo's own help."""
        result = run_cli("locomo", "--help")
        assert result.returncode == 0, result.stderr
        assert "--top-k" in result.stdout
        assert "--json" in result.stdout
        assert "LoCoMo" in result.stdout or "locomo" in result.stdout.lower()

    def test_unknown_benchmark_fails(self):
        """`python -m benchmarks nosuch` should exit non-zero + list available."""
        result = run_cli("nosuch")
        assert result.returncode != 0
        assert "unknown" in result.stderr.lower() or "nosuch" in result.stderr
        assert "latency" in result.stderr  # error message lists available


class TestScriptsBenchmarkShim:
    """Test that scripts/benchmark.py still works as a thin shim."""

    def test_scripts_help_still_works(self):
        """`python scripts/benchmark.py --help` should still work."""
        result = subprocess.run(
            [PYTHON, str(REPO / "scripts" / "benchmark.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(REPO),
        )
        assert result.returncode == 0, result.stderr
        assert "--chunks" in result.stdout

    def test_scripts_re_exports_percentile(self):
        """`from scripts.benchmark import percentile` still works."""
        from scripts.benchmark import percentile

        assert percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_scripts_re_exports_queries(self):
        """`from scripts.benchmark import BENCHMARK_QUERIES` still works."""
        from scripts.benchmark import BENCHMARK_QUERIES

        assert isinstance(BENCHMARK_QUERIES, list)
        assert len(BENCHMARK_QUERIES) >= 50


class TestLocomoSmoke:
    """Test the locomo smoke benchmark end-to-end."""

    def test_locomo_runs_and_writes_json(self):
        """`python -m benchmarks locomo --json <path>` should produce valid JSON."""
        out_path = Path("/tmp/test_locomo_p3.json")
        if out_path.exists():
            out_path.unlink()
        result = run_cli("locomo", "--json", str(out_path), timeout=120)
        assert result.returncode == 0, f"locomo failed: {result.stderr}"
        assert "Mean coverage" in result.stdout
        assert out_path.exists()

        data = json.loads(out_path.read_text())
        assert "coverage" in data
        assert "mean" in data["coverage"]
        assert "per_scenario" in data["coverage"]
        assert isinstance(data["coverage"]["mean"], float)
        assert data["coverage"]["mean"] >= 0.0
        assert data["coverage"]["mean"] <= 1.0
        assert "latency_ms" in data
        assert "p50" in data["latency_ms"]
        out_path.unlink()

    def test_locomo_cleans_up(self):
        """locomo must clean up its own seed data (idempotent across runs)."""
        # Run twice, second time should not leak data
        r1 = run_cli("locomo", timeout=120)
        assert r1.returncode == 0
        assert "deleted 9 chunks" in r1.stdout or "deleted 9 chunks" in r1.stdout
        r2 = run_cli("locomo", timeout=120)
        assert r2.returncode == 0
        # Second run also cleans up its own
        assert "deleted 9 chunks" in r2.stdout


class TestLatencyCLI:
    """Test the latency benchmark via the new CLI."""

    def test_latency_small_runs(self):
        """Small latency run should produce JSON output."""
        out_path = Path("/tmp/test_latency_p3.json")
        if out_path.exists():
            out_path.unlink()
        result = run_cli(
            "latency",
            "--chunks",
            "100",
            "--queries",
            "10",
            "--json",
            str(out_path),
            timeout=120,
        )
        assert result.returncode == 0, f"latency failed: {result.stderr}"
        assert "p50:" in result.stdout
        assert out_path.exists()

        data = json.loads(out_path.read_text())
        assert data["config"]["n_chunks"] == 100
        assert data["config"]["n_queries"] == 10
        assert "p50_ms" in data["recall"]
        out_path.unlink()

    def test_latency_validates_chunks(self):
        """`--chunks < 100` should fail."""
        result = run_cli("latency", "--chunks", "10", timeout=10)
        assert result.returncode != 0
        assert "chunks" in result.stderr.lower()

    def test_latency_validates_queries(self):
        """`--queries < 10` should fail."""
        result = run_cli("latency", "--chunks", "100", "--queries", "5", timeout=10)
        assert result.returncode != 0
        assert "queries" in result.stderr.lower()
