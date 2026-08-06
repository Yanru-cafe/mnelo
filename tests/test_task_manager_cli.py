"""
[8/6 M3 Step 13] Integration test for scripts/task_manager.py CLI.

走 subprocess 调 CLI (跟 CLI 用法一致). 验证:
  - create task / create loop
  - list task / list loop
  - move (transition)
  - replay
  - tick
"""
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "scripts" / "task_manager.py"
_PY = "/Users/apple/hermes-agent/venv/bin/python3"


def _run(args: list, *, env_extra: dict = None) -> tuple:
    """Run CLI; return (returncode, stdout, stderr).

    embedder 提示走 stdout, 但在 JSON 之前. 解析时找第一个 '{' 或 '[' 起点.
    """
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "MNELO_MEMORY_SEARCH_BACKEND": "usearch",
    }
    if env_extra:
        env.update(env_extra)
    cmd = [_PY, str(_CLI)] + args
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout, p.stderr


def _extract_json(stdout: str):
    """从 CLI stdout 找第一个 '{' 起点 (跳过 embedder 噪声行), 解析 JSON.

    策略: 行扫描, 跳 '[' 起头的 embedder 行, 第一个 '{' 起头才是 JSON.
    然后 brace-balance 直到 close.
    """
    lines = stdout.split("\n")
    # 跳到第一个 '{' 行
    start_idx = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("{"):
            start_idx = i
            break
    buf = lines[start_idx:]
    # brace-balance 直到 close
    depth = 0
    out_lines = []
    for line in buf:
        out_lines.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0 and "}" in line:
            break
    if not out_lines:
        raise ValueError(f"no JSON object in stdout: {stdout[:300]}")
    return json.loads("\n".join(out_lines))


def _setup():
    """Pre-clean via direct SQLite (CLI 自己没 --delete)."""
    import sqlite3
    db = Path("/Users/apple/.hermes/memory/memory.db")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "DELETE FROM task_states WHERE task_id LIKE 'task:20260806-cli-%' "
            "OR task_id LIKE 'loop:cli-%'"
        )
        conn.execute(
            "DELETE FROM entities WHERE id LIKE 'task:20260806-cli-%' "
            "OR id LIKE 'loop:cli-%'"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()


def test_cli_create_task():
    _setup()
    rc, out, err = _run([
        "create", "--kind", "task", "--name", "cli-a",
        "--now", "2026-08-06T10:00",
    ])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["task_id"] == "task:20260806-cli-a"
    assert data["current_state"] == "open"


def test_cli_create_loop():
    _setup()
    rc, out, err = _run([
        "create", "--kind", "loop", "--name", "cli-l",
        "--trigger", "x", "--interval-hours", "12",
        "--now", "2026-08-06T09:00",
    ])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["loop_id"] == "loop:cli-l"
    assert data["enabled"] is True
    assert data["interval_hours"] == 12


def test_cli_create_loop_disabled():
    _setup()
    rc, out, err = _run([
        "create", "--kind", "loop", "--name", "cli-dormant",
        "--trigger", "x", "--disabled",
        "--now", "2026-08-06T09:00",
    ])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["enabled"] is False


def test_cli_move():
    _setup()
    # 建 task
    rc, out, _ = _run([
        "create", "--kind", "task", "--name", "cli-move",
        "--now", "2026-08-06T10:00",
    ])
    tid = _extract_json(out)["task_id"]

    # move
    rc, out, err = _run([
        "move", tid, "--to", "in_progress",
        "--reason", "start",
        "--now", "2026-08-06T10:05",
    ])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["from_state"] == "open"
    assert data["to_state"] == "in_progress"


def test_cli_list_tasks():
    _setup()
    rc, out, _ = _run([
        "create", "--kind", "task", "--name", "cli-list1",
        "--now", "2026-08-06T10:00",
    ])
    rc, out, _ = _run([
        "create", "--kind", "task", "--name", "cli-list2",
        "--now", "2026-08-06T10:01",
    ])
    rc, out, err = _run(["list", "--kind", "task", "--limit", "50"])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    names = [t["name"] for t in data["tasks"]]
    assert "cli-list1" in names
    assert "cli-list2" in names


def test_cli_replay():
    _setup()
    rc, out, _ = _run([
        "create", "--kind", "task", "--name", "cli-replay",
        "--now", "2026-08-06T10:00",
    ])
    tid = _extract_json(out)["task_id"]
    _run(["move", tid, "--to", "in_progress",
          "--reason", "start", "--now", "2026-08-06T10:05"])

    rc, out, err = _run(["replay", tid])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["current_state"] == "in_progress"
    assert data["window_count"] == 2


def test_cli_tick_due():
    _setup()
    rc, out, _ = _run([
        "create", "--kind", "loop", "--name", "cli-tick",
        "--trigger", "x", "--now", "2026-08-06T09:00",
    ])
    lid = _extract_json(out)["loop_id"]

    rc, out, err = _run(["tick", lid, "--now", "2026-08-06T10:00"])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    assert data["verdict"] == "due"


def test_cli_loop_list_enabled_only():
    _setup()
    _run(["create", "--kind", "loop", "--name", "cli-on",
          "--trigger", "x", "--now", "2026-08-06T09:00"])
    _run(["create", "--kind", "loop", "--name", "cli-off",
          "--trigger", "x", "--disabled", "--now", "2026-08-06T09:01"])

    rc, out, err = _run(["list", "--kind", "loop", "--enabled-only"])
    assert rc == 0, f"rc={rc}, err={err}"
    data = _extract_json(out)
    names = [l["name"] for l in data["loops"]]
    assert "cli-on" in names
    assert "cli-off" not in names
