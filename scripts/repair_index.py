#!/usr/bin/env python3
"""A7 — scripts/repair_index.py (TASKS_SEARCH_INDEX §4 A7).

索引修复 + 完整性校验 (orphan vector 删除).
Q1 孤儿向量: usearch/zvec 索引写入在 SQLite 事务外 — 若 remember SQLite 侧最终
ROLLBACK, 索引留下指向不存在 chunk 的向量.

usage: python scripts/repair_index.py [--backend usearch|sqlite_vec|auto] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from search_index import (  # noqa: E402
    SQLiteVecIndex, UsearchIndex, ZvecIndex,
    build_search_index, usearch_available, zvec_available,
)


def _iter_index_ids_usearch(idx: UsearchIndex):
    """遍历 usearch 索引内所有 id (uint64 rowid)."""
    return [int(k) for k in idx._index.keys]


def _iter_index_ids_sqlite_vec(conn):
    """sqlite_vec: rowid 即索引 id, 遍历 chunks rowid 已 commit."""
    return [r[0] for r in conn.execute("SELECT rowid FROM chunks WHERE valid_until IS NULL")]


def _iter_index_ids_zvec(idx: ZvecIndex):
    """zvec: doc.id 直接是 chunk_id."""
    return [d.id for d in idx._col.iter_all()]


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
        # 不同后端的索引 id → 查 chunk_id 的方式
        if isinstance(idx, UsearchIndex):
            rowids = _iter_index_ids_usearch(idx)
            # rowid → chunk_id 映射查 SQLite
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
        elif isinstance(idx, SQLiteVecIndex):
            # sqlite_vec: 索引 = chunks.rowid 1:1, 跟 SQLite chunks 同步; 正常无孤儿
            rowids = _iter_index_ids_sqlite_vec(conn)
            kept = len(rowids)
            deleted = 0
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
    ap = argparse.ArgumentParser(description="Repair mnelo search index (orphan vector cleanup)")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "sqlite_vec", "usearch", "zvec"],
                    help="目标后端 (默认 auto: zvec > usearch > sqlite_vec)")
    ap.add_argument("--dry-run", action="store_true", help="只报数, 不真删")
    ap.add_argument("--db", default=None, help="db 路径 (默认 <repo>/memory.db)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else (ROOT / "memory.db")
    stats = repair(args.backend, db_path, dry_run=args.dry_run)
    print(stats)


if __name__ == "__main__":
    main()