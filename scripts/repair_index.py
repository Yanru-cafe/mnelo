#!/usr/bin/env python3
"""A7 — scripts/repair_index.py (TASKS_SEARCH_INDEX §4 A7 + 8/6 plan §5).

索引修复 + 完整性校验 (orphan vector 删除).
Q1 孤儿向量: usearch/zvec 索引写入在 SQLite 事务外 — 若 remember SQLite 侧最终
ROLLBACK, 索引留下指向不存在 chunk 的向量.

[8/6 plan] 向量库二选一: 删 SQLiteVecIndex 分支. backend ∈ {auto, usearch, zvec}.

usage: python scripts/repair_index.py [--backend usearch|zvec|auto] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from search_index import (  # noqa: E402
    UsearchIndex, ZvecIndex,
    build_search_index, usearch_available, zvec_available,
)
from config import config as _config  # noqa: E402


def _iter_index_ids_usearch(idx: UsearchIndex):
    """遍历 usearch 索引内所有 id (uint64 rowid)."""
    return [int(k) for k in idx._index.keys]


def _iter_index_ids_zvec(idx: ZvecIndex):
    """zvec: doc.id 直接是 chunk_id."""
    try:
        return [d.id for d in idx._col.iter_all()]
    except Exception as e:
        print(f"[repair_index] zvec.iter_all failed: {e}", file=sys.stderr)
        return []


def repair(backend: str, db_path: Path, dry_run: bool = False) -> dict:
    """遍历索引 → 查 SQLite 活跃 chunks → 删无对应项 (orphan). Returns stats."""
    if not db_path.exists():
        raise FileNotFoundError(f"db not found: {db_path}")

    idx = build_search_index(backend, db_path, dim=512)
    deleted = 0
    kept = 0
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # 取所有活跃 chunk_id
        alive = set(
            r[0] for r in conn.execute(
                "SELECT id FROM chunks WHERE valid_until IS NULL"
            )
        )
        if isinstance(idx, UsearchIndex):
            rowids = _iter_index_ids_usearch(idx)
            import numpy as _np
            for rid in rowids:
                row = conn.execute(
                    "SELECT id FROM chunks WHERE rowid = ? AND valid_until IS NULL",
                    (rid,),
                ).fetchone()
                if row:
                    kept += 1
                else:
                    if not dry_run:
                        idx._index.remove(_np.array([rid], dtype=_np.uint64))
                    deleted += 1
        elif isinstance(idx, ZvecIndex):
            ids = _iter_index_ids_zvec(idx)
            for cid in ids:
                if cid in alive:
                    kept += 1
                else:
                    if not dry_run:
                        idx._col.delete([cid])
                    deleted += 1
        conn.close()
    finally:
        idx.close()

    return {
        "backend": backend,
        "backend_resolved": idx.name,
        "kept": kept,
        "deleted": deleted,
        "dry_run": dry_run,
    }


def main():
    ap = argparse.ArgumentParser(description="Repair mnelo search index (orphan vector cleanup). [8/6] 后端感知 (usearch/zvec).")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "usearch", "zvec"],
                    help="目标后端 (默认 auto: zvec > usearch; 都不可用 RuntimeError)")
    ap.add_argument("--dry-run", action="store_true", help="只报数, 不真删")
    ap.add_argument("--db", default=None, help="db 路径 (默认从 config 解析)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(_config.db_path)
    stats = repair(args.backend, db_path, dry_run=args.dry_run)
    print(stats)


if __name__ == "__main__":
    main()