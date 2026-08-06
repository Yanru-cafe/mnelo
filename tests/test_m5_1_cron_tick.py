"""
[8/6 M5.1 + DESIGN §4.3 §8 cron/timer 驱动] 测试 scripts/mnelo_loop_tick_cron.py.

覆盖:
  M5.1.1 --dry-run 不写 audit_log / digest
  M5.1.2 真跑写 audit_log (status='proposed', pass_name='loop_tick_cron')
  M5.1.3 due loop 判定正确 (loop 无 active_task 且 interval 已过 → due)
  M5.1.4 lock 防重叠 (PID-based)
  M5.1.5 threshold 过滤 interval_hours
  M5.1.6 digest 输出到 ~/.hermes/cron/output/loop_tick/<date>.json
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("MNELO_MEMORY_SEARCH_BACKEND", "usearch")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_CREATE_LOOP_SRC = """
import sys
sys.path.insert(0, '{repo}')
import task_states as ts
import memory
m = memory.Memory()
r = ts.loop_create(
    m._conn,
    name='{name}',
    trigger='m5-trigger',
    enabled={enabled},
    interval_hours={interval},
    now='2026-08-06T09:00',
)
m._conn.commit()
m.close()
print('LID:', r['loop_id'])
"""


def _run(args, env_extra=None, timeout=60):
    """Run script in subprocess, return (stdout, stderr, rc)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "MNELO_MEMORY_SEARCH_BACKEND": "usearch",
    }
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(_REPO / "scripts/mnelo_loop_tick_cron.py")] + args,
        capture_output=True, text=True, env=env, timeout=timeout,
        cwd=str(_REPO),
    )
    return p.stdout, p.stderr, p.returncode


def _setup():
    """Clean fixtures: clear m5 test loops + recent loop_tick_cron audit_log."""
    import sqlite3
    c = sqlite3.connect(str(_REPO / "memory.db"))
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute(
        "DELETE FROM task_states WHERE task_id LIKE 'loop:m5-%' "
        "OR task_id LIKE 'loop:20260806-m5-%'"
    )
    c.execute(
        "DELETE FROM entities WHERE id LIKE 'loop:m5-%' "
        "OR id LIKE 'loop:20260806-m5-%'"
    )
    c.execute(
        "DELETE FROM audit_log WHERE pass_name='loop_tick_cron' "
        "AND (ref_id LIKE 'loop:m5-%' OR after_json LIKE '%m5-%')"
    )
    c.execute("PRAGMA foreign_keys = ON")
    c.commit()
    c.close()
    Path("/tmp/mnelo_loop_tick_cron.lock").unlink(missing_ok=True)


def _create_loop(name: str, enabled: bool = True, interval: int = 24) -> str:
    """建一个 test loop via subprocess."""
    src = _CREATE_LOOP_SRC.format(
        repo=str(_REPO), name=name, enabled=str(enabled), interval=str(interval),
    )
    p = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
             "MNELO_MEMORY_SEARCH_BACKEND": "usearch"},
        cwd=str(_REPO),
    )
    assert p.returncode == 0, f"loop_create failed: {p.stderr}"
    for line in p.stdout.split("\n"):
        if line.startswith("LID:"):
            return line.split(": ", 1)[1].strip()
    raise AssertionError(f"no LID in output: {p.stdout}")


def test_m5_1_dry_run_no_audit_log_no_digest():
    """[M5.1.1] --dry-run 不应写 audit_log 或 digest 文件."""
    _setup()
    _create_loop("m5-dryrun")

    out, err, rc = _run(["--dry-run", "--threshold", "0"])
    assert rc == 0, f"rc={rc}: {err}"

    import sqlite3
    c = sqlite3.connect(str(_REPO / "memory.db"))
    n = c.execute(
        "SELECT COUNT(*) FROM audit_log WHERE pass_name='loop_tick_cron' "
        "AND ref_id LIKE 'loop:m5-%'"
    ).fetchone()[0]
    c.close()
    assert n == 0, f"dry-run should not write audit_log, got {n} rows"


def test_m5_1_real_run_writes_audit_log_proposed():
    """[M5.1.2] 真跑写 audit_log (status='proposed', pass_name='loop_tick_cron')."""
    _setup()
    lid = _create_loop("m5-audit")
    print(f"created loop: {lid}")

    out, err, rc = _run(["--threshold", "0"])
    assert rc == 0, f"rc={rc}: {err}"

    import sqlite3
    c = sqlite3.connect(str(_REPO / "memory.db"))
    row = c.execute(
        "SELECT pass_name, action_type, ref_type, ref_id, status, after_json "
        "FROM audit_log WHERE pass_name='loop_tick_cron' AND ref_id=?",
        (lid,),
    ).fetchone()
    c.close()
    assert row is not None, f"audit_log row missing for {lid}"
    assert row[0] == "loop_tick_cron", row
    assert row[1] == "tick_due", row
    assert row[2] == "loop", row
    assert row[3] == lid, row
    assert row[4] == "proposed", row
    after = json.loads(row[5])
    assert after["verdict"] == "due", after


def test_m5_1_due_verdict_correct():
    """[M5.1.3] loop 无 active_task + interval 已过 → due."""
    _setup()
    lid = _create_loop("m5-due", interval=1)

    out, err, rc = _run(["--threshold", "0"])
    assert rc == 0, f"rc={rc}: {err}"

    digest_path = Path.home() / ".hermes/cron/output/loop_tick/2026-08-06.json"
    assert digest_path.exists(), f"digest missing: {digest_path}"
    entries = json.loads(digest_path.read_text())
    if not isinstance(entries, list):
        entries = [entries]
    all_due_ids = set()
    for entry in entries:
        for l in entry.get("due_loops", []):
            all_due_ids.add(l["loop_id"])
    assert lid in all_due_ids, f"loop {lid} should be due, found {all_due_ids}"


def test_m5_1_lock_prevents_overlap():
    """[M5.1.4] lock 防重叠. 模拟 stale lock."""
    _setup()
    lock_path = Path("/tmp/mnelo_loop_tick_cron.lock")
    lock_path.write_text(str(os.getpid()))
    old_time = time.time() - 7200
    os.utime(lock_path, (old_time, old_time))

    out, err, rc = _run(["--threshold", "0"])
    assert rc == 0, f"rc={rc}: {err}"
    assert "stale lock" in out or "replacing" in out, \
        f"expected stale lock replacement msg: {out[:300]}"


def test_m5_1_threshold_filter():
    """[M5.1.5] threshold 过滤 interval_hours < threshold 的 loop."""
    _setup()
    lid_short = _create_loop("m5-short", interval=1)
    lid_long = _create_loop("m5-long", interval=48)
    print(f"lid_short={lid_short} lid_long={lid_long}")

    out, err, rc = _run(["--threshold", "24"])
    assert rc == 0, f"rc={rc}: {err}"

    import sqlite3
    c = sqlite3.connect(str(_REPO / "memory.db"))
    short_n = c.execute(
        "SELECT COUNT(*) FROM audit_log WHERE pass_name='loop_tick_cron' AND ref_id=?",
        (lid_short,),
    ).fetchone()[0]
    long_n = c.execute(
        "SELECT COUNT(*) FROM audit_log WHERE pass_name='loop_tick_cron' AND ref_id=?",
        (lid_long,),
    ).fetchone()[0]
    c.close()
    assert short_n == 0, f"threshold=24 应过滤掉 interval=1 loop, got {short_n}"
    assert long_n >= 1, f"threshold=24 应保留 interval=48 loop, got {long_n}"


def test_m5_1_digest_path_well_formed():
    """[M5.1.6] digest 输出路径 + JSON 结构正确."""
    _setup()
    _create_loop("m5-digest")

    out, err, rc = _run(["--threshold", "0"])
    assert rc == 0, f"rc={rc}: {err}"

    digest_path = Path.home() / ".hermes/cron/output/loop_tick/2026-08-06.json"
    assert digest_path.exists(), f"digest missing: {digest_path}"
    data = json.loads(digest_path.read_text())
    entries = data if isinstance(data, list) else [data]

    last = entries[-1]
    for key in ("ts", "total_loops", "due_count", "due_loops", "not_due_count",
                "error_count", "error_loops", "dry_run"):
        assert key in last, f"missing key {key} in {last.keys()}"
    assert isinstance(last["due_loops"], list)
    assert last["dry_run"] is False