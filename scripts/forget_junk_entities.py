#!/usr/bin/env python3
"""
forget_junk_entities.py — 批量 soft-delete HonchoImporter 噪声 entity.

设计:
- 直连 SQLite (绕开 zvec LOCK, 因为 mcp_server 持 LOCK 跑 zvec)
- entity forget 不动 _index (memory.py:717-720 只 SQL UPDATE)
- 30 天 purge queue 入队 + audit_log 写 — 保留 audit_window 完整
- cascade 自动级联关系 (anno:* entity 大概率没真关系, 但仍走 cascade)

[8/8 P1] 主人授权 forget A 类全部 (~4147 条):
  - anno: 开头 (HonchoImporter NER 噪声, ~4124 条)
  - TOKEN_C_* (随机 token, ~70 条)
  - 长句子 / 路径 entity (~76 条)

用法:
  MNELO_HOME=~/.hermes python3 scripts/forget_junk_entities.py [--dry-run] [--limit N] [--pattern anno:]
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import config as _config  # noqa: E402

DB_PATH = Path(_config.db_path)


def now_iso() -> str:
    """memory.py 里 now() 的简化版本, ISO8601 + tz."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def list_junk(conn: sqlite3.Connection, pattern: str, limit: int | None) -> list[tuple[str, str]]:
    """[8/8 P1 fix] HonchoImporter 噪声 entity 用 id LIKE 而非 name LIKE.
    实际 schema: id='anno:mentions:CLI', name='CLI'. name LIKE 'anno:%' = 0 匹配.
    """
    rows = conn.execute(
        "SELECT id, name FROM entities "
        "WHERE kind='concept' AND valid_until IS NULL AND id LIKE ?",
        (pattern + "%",),
    ).fetchall()
    if limit:
        rows = rows[:limit]
    return [(r[0], r[1]) for r in rows]


def forget_one(conn: sqlite3.Connection, eid: str, reason: str) -> tuple[int, int]:
    """模拟 Memory.forget(target_kind='entity') 路径, 不动 _index.

    Returns: (updated_entities, edges_invalidated).
    """
    ts = now_iso()
    cur = conn.execute(
        "UPDATE entities SET valid_until = ? "
        "WHERE id = ? AND valid_until IS NULL",
        (ts, eid),
    )
    updated = cur.rowcount
    if updated == 0:
        return (0, 0)
    cur = conn.execute(
        "UPDATE relations SET valid_until = ? "
        "WHERE (source_id = ? OR target_id = ?) AND valid_until IS NULL",
        (ts, eid, eid),
    )
    edges = cur.rowcount
    conn.execute(
        "INSERT INTO purged_queue (target_id, target_kind, purged_at, done) "
        "VALUES (?, 'entity', datetime('now', '+30 days'), 0)",
        (eid,),
    )
    # 写 audit_log (跟 L2 hygiene pass 同结构, status='applied')
    import uuid
    run_id = f"junk_forget_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    conn.execute(
        "INSERT INTO audit_log (run_id, pass_name, action_type, ref_type, ref_id, "
        "before_json, after_json, llm_used, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'applied', ?)",
        (
            run_id,
            "manual_junk_forget",
            f"soft_delete_{reason}",
            "entity",
            eid,
            None,  # before = original state (skipped for brevity)
            f'{{"valid_until": "{ts}", "edges_invalidated": {edges}}}',
            ts,
        ),
    )
    return (updated, edges)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pattern", default="anno:")
    ap.add_argument("--reason", default="honcho_importer_noise_2026-08-08")
    args = ap.parse_args()

    print(f"[forget_junk] pattern={args.pattern!r} limit={args.limit} dry_run={args.dry_run}")

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")
    targets = list_junk(conn, args.pattern, args.limit)
    print(f"[forget_junk] matched {len(targets)} active entity:")
    for eid, ename in targets[:5]:
        print(f"  sample: {eid} | {ename[:60]}")
    if len(targets) > 5:
        print(f"  ... and {len(targets) - 5} more")

    if args.dry_run:
        conn.close()
        print("[forget_junk] DRY-RUN, no changes made.")
        return

    # 真 forget
    t0 = time.time()
    total_updated = 0
    total_edges = 0
    failed = 0
    # 每 100 条 commit 一次, 避免 long transaction 锁 WAL
    BATCH = 100
    for i, (eid, _) in enumerate(targets, 1):
        try:
            u, e = forget_one(conn, eid, args.reason)
            total_updated += u
            total_edges += e
        except Exception as exc:
            failed += 1
            print(f"[forget_junk] FAILED {eid}: {exc}")
        if i % BATCH == 0:
            conn.commit()
            print(f"[forget_junk] progress: {i}/{len(targets)} "
                  f"({total_updated} entities, {total_edges} edges, {failed} failed)")
    conn.commit()
    elapsed = time.time() - t0
    print(f"[forget_junk] DONE: {total_updated}/{len(targets)} forgotten, "
          f"{total_edges} edges invalidated, {failed} failed, {elapsed:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
