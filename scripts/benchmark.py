#!/usr/bin/env python3
"""
scripts/benchmark.py — thin shim for backward compatibility.

[8/11 P3-benchmarks] 实现在 `benchmarks/latency.py`，本脚本保留为薄壳：
- `from scripts.benchmark import percentile / BENCHMARK_QUERIES` 仍然可用
  （test_benchmark_round15.py 依赖）
- `python scripts/benchmark.py [args]` 转发到 `python -m benchmarks latency [args]`

新代码请直接用 `python -m benchmarks latency` 入口。

历史：
- 7/19 v0.5.5 起 scripts/benchmark.py 是 latency benchmark 唯一入口
- 8/11 P3 任务卡要求把入口提升到 `python -m benchmarks <name>` 风格（对标
  mem0 memory-benchmarks），但保留旧脚本路径作为过渡，让现有测试 / CI /
  README 引用不破坏。
"""

from __future__ import annotations

import sys
from pathlib import Path

# [8/11 P3] shim needs to find the `benchmarks` package when invoked as
# `python scripts/benchmark.py` (Python adds scripts/ to sys.path, not the
# repo root). Original scripts/benchmark.py same trick.
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Re-export for backward compat (tests/test_benchmark_round15.py imports these)
from benchmarks.latency import (
    BENCHMARK_QUERIES,
    cleanup_seed,
    percentile,
    run_benchmark,
    seed_chunks,
)

__all__ = [
    "BENCHMARK_QUERIES",
    "percentile",
    "seed_chunks",
    "cleanup_seed",
    "run_benchmark",
]


def main() -> int:
    """Forward to benchmarks.latency.main() — same CLI, same flags."""
    from benchmarks.latency import main as _main

    return _main()


if __name__ == "__main__":
    sys.exit(main())
