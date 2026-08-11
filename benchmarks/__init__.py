"""mnelo public evaluation harness — 可复跑 benchmark 包.

借鉴 mem0 memory-benchmarks 模式 (docs/research/mem0-comparison.md 借鉴 #6):
BENCHMARKS.md 只记录静态数字, 本包把测量变成可复现命令:

    python -m benchmarks latency --chunks 10000 --queries 100 --json bench.json

"anyone can reproduce" — 数字可直接由命令复跑生成.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
