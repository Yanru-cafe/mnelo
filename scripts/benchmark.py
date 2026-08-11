#!/usr/bin/env python3
"""[P3-benchmarks] 薄包装 — 新入口: python -m benchmarks latency.

核心逻辑已迁移到 benchmarks/latency.py (2026-08-11). 本文件保留旧命令兼容
(python scripts/benchmark.py ...), 避免破坏 docs/BENCHMARKS.md 引用与既有调用.

Usage:
  python -m benchmarks latency --chunks 10000 --queries 100 --json bench.json
  python scripts/benchmark.py --chunks 10000 --queries 100   # 等价旧入口
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.latency import main  # noqa: E402  (依赖上方 sys.path 注入)

if __name__ == "__main__":
    sys.exit(main())
