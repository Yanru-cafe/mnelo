"""`python -m benchmarks` — mnelo evaluation harness 入口.

子命令分发. 当前支持:

    python -m benchmarks latency [--chunks N] [--queries N] [--top-k K] [--json PATH]

无子命令/未知子命令 → 打印 usage, exit 2.
"""

import sys

_SUBCOMMANDS = ("latency",)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in _SUBCOMMANDS:
        print(
            "mnelo evaluation harness\n"
            "\n"
            "usage: python -m benchmarks <command> [options]\n"
            "\n"
            "commands:\n"
            "  latency   recall 延迟 benchmark (--chunks N --queries N --top-k K --json PATH)\n",
            file=sys.stderr,
        )
        return 2

    cmd = argv.pop(0)
    if cmd == "latency":
        from benchmarks.latency import main as latency_main

        return latency_main(argv)
    return 2


if __name__ == "__main__":
    sys.exit(main())
