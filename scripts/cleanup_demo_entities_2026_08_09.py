#!/usr/bin/env python3
"""
cleanup_demo_entities_2026_08_09.py — [8/9 燕如 P5 反馈] 清 demo entities.

燕如 8/9 P5 实证报告: mnelo recall 不带 source filter 时看到 ~8 个 demo stock
entities. 实测 demo 实际有 658 条 (~88% of active entities), 都是
main_block_demo_* prefix, 来自 v0.5.x 早期 main_block_demo_stock / person 教程脚本.

[策略] 软删 (mnelo_forget → valid_until = now), 走 30 天 audit_undo window.
[为什么软删不用物理 DELETE] SOUL §mnelo ops #3: purge_backlog 是 TTL 30 天延迟
队列 design, destructive 真清绕过 audit window 失去 memory_audit_undo 保护.

[过滤] id LIKE 'main_block_demo_%' (658 条都属此 prefix). user_confirmed=0
不能当 demo 标志 (主人真实 stock 也都是 user_confirmed=0).

[已知坑]
- main_block_demo_* 是 7/19 早期 main_block tutorial 脚本产物.
  清理后 script 05_main_block_demo_*.py 等 main_block.py 教程脚本会建
  新的 demo, 报冲突. 教程脚本应该也清掉或改 demo id 模板.
- 软删后 30 天内 memory_audit_undo 可恢复 (audit_log 留痕).
- 跑前用 --dry-run 看名单, --yes 真跑.
"""
import argparse
import sqlite3
import sys
from pathlib import Path


DB_PATH = Path("~/.hermes/memory/memory.db").expanduser()
DEMO_PREFIX = "main_block_demo_"


def get_demo_entities(conn: sqlite3.Connection) -> list:
    """返回所有 active demo entities (id 匹配 main_block_demo_*)."""
    cur = conn.execute(
        "SELECT id, kind, source, importance, user_confirmed FROM entities "
        "WHERE valid_until IS NULL AND id LIKE ? ORDER BY id",
        (DEMO_PREFIX + "%",),
    )
    return cur.fetchall()


def soft_delete_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    """[8/9] 软删 entity: valid_until = now. 30 天 audit_undo window."""
    conn.execute(
        "UPDATE entities SET valid_until = datetime('now', 'localtime') "
        "WHERE id = ? AND valid_until IS NULL",
        (entity_id,),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="[8/9 燕如 P5 反馈] 清 demo entities (main_block_demo_*)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="mnelo db path")
    parser.add_argument("--dry-run", action="store_true", help="只列名单, 不改 db")
    parser.add_argument("--yes", action="store_true", help="真删 (默认 dry-run)")
    parser.add_argument("--audit-log", default="~/.hermes/memory/cleanup_demo_audit.log",
                        help="audit log path (写入每条删除 entity id)")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"✗ db not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        demo_entities = get_demo_entities(conn)
        if not demo_entities:
            print("✓ no demo entities to clean")
            return 0

        # 按 kind 分组统计
        by_kind: dict = {}
        for ent in demo_entities:
            by_kind[ent[1]] = by_kind.get(ent[1], 0) + 1

        print(f"=== Demo entities cleanup ===")
        print(f"db: {db_path}")
        print(f"total demo entities: {len(demo_entities)}")
        print(f"by kind:")
        for kind, cnt in sorted(by_kind.items()):
            print(f"  {kind}: {cnt}")
        print()

        # 显示前 10 个样本
        print(f"sample (first 10):")
        for ent in demo_entities[:10]:
            print(f"  {ent[0]} | {ent[1]} | src={ent[2] or '-'} | imp={ent[3]} | confirmed={ent[4]}")
        if len(demo_entities) > 10:
            print(f"  ... and {len(demo_entities) - 10} more")
        print()

        if not args.yes:
            print("=== DRY RUN (--yes to actually delete) ===")
            print(f"将软删 {len(demo_entities)} 条 entities (valid_until = now)")
            print(f"audit log: {args.audit_log}")
            return 0

        # 真删
        print(f"=== DELETING ===")
        audit_log = Path(args.audit_log).expanduser()
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_log, "a") as f:
            f.write(f"\n=== [{demo_entities[0][0]}-...] cleanup_demo_entities_2026_08_09 run, {len(demo_entities)} entities ===\n")
            for ent in demo_entities:
                f.write(f"  soft_delete {ent[0]} | {ent[1]}\n")

        for i, ent in enumerate(demo_entities, 1):
            soft_delete_entity(conn, ent[0])
            if i % 100 == 0:
                print(f"  {i}/{len(demo_entities)} deleted")
        conn.commit()
        print(f"✓ {len(demo_entities)} entities soft-deleted")
        print(f"audit log: {audit_log}")
        print(f"30 天内可用 memory_audit_undo 恢复")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
