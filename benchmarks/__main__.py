"""benchmarks.__main__ — CLI dispatcher for `python -m benchmarks <name>`.

[8/11 P3-benchmarks] 对标 mem0 memory-benchmarks 的 public evaluation harness 模式:

  python -m benchmarks latency [...]   # 召回延迟 (p50 / p95 / p99)
  python -m benchmarks locomo [...]    # LoCoMo 风格召回质量 smoke
  python -m benchmarks --list          # 列出所有 benchmark
  python -m benchmarks --help          # 显示用法

子模块由 `BENCHMARKS` 注册表决定；新增 benchmark 在这里登记一行即可。

---

设计注意：dispatcher 自己有 `--help` / `--list`，但 `--help` 必须能**穿透**到
子模块（即 `python -m benchmarks locomo --help` 要显示 locomo 自己的用法）。
因此 dispatcher 用手写 argv 分发，而不是 `parse_known_args`，因为后者会吞掉
`--help` 触发 dispatcher 自己的 help 印刷。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so submodules can `from memory import Memory`
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Benchmark registry: name -> (module, description)
BENCHMARKS: dict[str, tuple[str, str]] = {
    "latency": ("benchmarks.latency", "mnelo recall latency (p50/p95/p99 @ N chunks)"),
    "locomo": ("benchmarks.locomo", "LoCoMo-style end-to-end recall coverage + latency smoke"),
}


def _print_dispatcher_help() -> None:
    """Top-level usage banner (printed when no subcommand given)."""
    print("usage: python -m benchmarks [--list] <name> [args]")
    print()
    print("mnelo public evaluation harness")
    print()
    print("options:")
    print("  --list     list all available benchmarks")
    print("  --help     show this help")
    print()
    print("Available benchmarks:")
    for name, (_module, desc) in sorted(BENCHMARKS.items()):
        print(f"  {name:10s}  {desc}")
    print()
    print("Run `python -m benchmarks <name> --help` for the benchmark's own usage.")


def _print_list() -> None:
    print("Available benchmarks:")
    for name, (module, desc) in sorted(BENCHMARKS.items()):
        print(f"  {name:10s}  {desc}")
        print(f"             (module: {module})")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Top-level --list (no subcommand)
    if argv and argv[0] == "--list":
        _print_list()
        return 0

    # Top-level --help / -h (when no subcommand)
    if not argv or (argv[0] in ("--help", "-h") and len(argv) == 1):
        _print_dispatcher_help()
        return 0

    # First positional is the benchmark name
    name = argv[0]
    if name not in BENCHMARKS:
        # argparse-style error
        print(f"error: unknown benchmark '{name}'", file=sys.stderr)
        avail = ", ".join(sorted(BENCHMARKS.keys()))
        print(f"  available: {avail}", file=sys.stderr)
        print("  run `python -m benchmarks --list` to see all", file=sys.stderr)
        return 2

    # Dispatch to submodule, forwarding the rest of argv
    module_name, _desc = BENCHMARKS[name]
    import importlib

    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        print(f"error: failed to import {module_name}: {e}", file=sys.stderr)
        return 1

    if not hasattr(mod, "main"):
        print(f"error: {module_name} has no main()", file=sys.stderr)
        return 1

    # Skip the benchmark name when forwarding to the submodule
    return mod.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
