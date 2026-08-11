"""mnelo benchmarks — public evaluation harness.

[8/11 P3-benchmarks] 借鉴 mem0 memory-benchmarks 模式，提供 `python -m benchmarks <name>` CLI
入口，让 README 引用的数字可复现（marketing 弹药）。

子模块:
- latency: 总召回延迟基准（p50 / p95 / p99 @ N chunks）
- locomo:  LoCoMo 风格召回质量评估（多轮对话 / 时序信号）

根目录 CLI 入口：python -m benchmarks <name> [args]
"""

__version__ = "1.0.0"
