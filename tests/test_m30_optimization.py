"""
[8/6 M30 review-pass] 3 个多角度优化测试.

覆盖:
  M30.1 [race fix] apply_stale_proposal 并发安全 — BEGIN IMMEDIATE 事务, 双
       并发 apply 第二个应抛 ProposalAlreadyResolved (而不是双写 audit_log)
  M30.2 [validation] propose_stale_tasks stale_days_threshold 必须正整数,
       负数 / 0 / 非 int 抛 InvalidThreshold
  M30.3 [digest contract] render_digest_block4 输出截断到 ≤2000 字符 (digest
       injection 契约, 防止 Agent context overflow)
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime as _dt
from datetime import timedelta as _td

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import os
os.environ.setdefault("MNELO_MEMORY_SEARCH_BACKEND", "usearch")

import memory
import task_states


def _setup():
    c = sqlite3.connect(str(memory.DB_PATH))
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("DELETE FROM task_states WHERE task_id LIKE 'task:m30-%' OR task_id LIKE 'loop:m30-%'")
    c.execute("DELETE FROM entities WHERE id LIKE 'task:m30-%' OR id LIKE 'loop:m30-%'")
    c.execute("DELETE FROM audit_log WHERE (pass_name='stuck_task' OR pass_name='forced_forget') AND (ref_id LIKE 'task:%m30-%' OR ref_id LIKE 'loop:%m30-%')")
    c.execute("PRAGMA foreign_keys = ON")
    c.commit()
    c.close()


def _create_stale_task(name: str, days_ago: int = 10) -> str:
    """建 open task, valid_from backdated."""
    m = memory.Memory()
    try:
        back = (_dt.now() - _td(days=days_ago)).isoformat(timespec="milliseconds")
        r = task_states.task_create(m._conn, name=name, now=back)
        tid = r["task_id"]
        m._conn.commit()
        return tid
    finally:
        m.close()


# ===== M30.1 race condition =====

def test_m30_1_apply_double_resolved_check_atomic():
    """[M30.1] apply_stale_proposal 重复 apply 应抛 ProposalAlreadyResolved.

    旧 bug: check + insert 非原子, 两并发 apply 第二个 INSERT UNIQUE 不冲突
    (run_id 含 proposal_id 不同). M30 修后: BEGIN IMMEDIATE 事务, SQLite
    序列化, 第二个 check 命中 stale_resolved/applied 抛错.
    """
    _setup()
    tid = _create_stale_task("m30-race-dup")

    c = sqlite3.connect(str(memory.DB_PATH))
    try:
        # propose
        result = task_states.propose_stale_tasks(c, now="2026-08-06T15:00")
        pid = None
        for p in result["proposals"]:
            if p["task_id"] == tid:
                # find audit_log id
                row = c.execute(
                    """SELECT id FROM audit_log
                       WHERE pass_name='stuck_task' AND ref_id=? AND status='proposed'""",
                    (tid,),
                ).fetchone()
                pid = row[0]
                break
        assert pid is not None

        # 第一次 apply 成功
        applied1 = task_states.apply_stale_proposal(
            c, pid, applied_action="first_apply",
        )
        assert applied1["status"] == "applied"

        # 第二次 apply 应抛 ProposalAlreadyResolved
        try:
            task_states.apply_stale_proposal(
                c, pid, applied_action="second_apply_attempt",
            )
            assert False, "second apply should raise"
        except task_states.TaskLoopError as e:
            assert e.code == "ProposalAlreadyResolved", f"expected ProposalAlreadyResolved, got {e.code}"

        # 校验 audit_log 只 1 行 stale_resolved/applied (不双写)
        n = c.execute(
            """SELECT COUNT(*) FROM audit_log
               WHERE pass_name='stuck_task' AND action_type='stale_resolved'
                 AND ref_id=? AND status='applied'""",
            (tid,),
        ).fetchone()[0]
        assert n == 1, f"应有 1 行 stale_resolved/applied, got {n}"
    finally:
        c.close()


def test_m30_1b_apply_second_proposal_blocked_by_resolved():
    """[M30.1b] 同一 task 第二次 apply 应被 ProposalAlreadyResolved 拒绝.

    M28.1 设计: 一旦 ref_id 走 applied, 后续所有 proposal_id 的 apply 都拒绝.
    不允许多次 apply (stale 状态可重新 propose, 但已 resolved 锁定).
    测试 M30 + M28.1 协作.
    """
    _setup()
    tid = _create_stale_task("m30-race-distinct")

    c = sqlite3.connect(str(memory.DB_PATH))
    try:
        # 第一次 propose + apply
        r1 = task_states.propose_stale_tasks(c, now="2026-08-06T10:00")
        row1 = c.execute(
            "SELECT id FROM audit_log WHERE pass_name='stuck_task' AND ref_id=? AND status='proposed'",
            (tid,),
        ).fetchone()
        assert row1 is not None
        pid1 = row1[0]
        task_states.apply_stale_proposal(c, pid1, applied_action="ignored_first_time")

        # 第二次 propose (M28 fix: apply 后允许再提议)
        r2 = task_states.propose_stale_tasks(c, now="2026-08-06T15:00")
        proposed_ids = [p["task_id"] for p in r2["proposals"]]
        assert tid in proposed_ids, f"M28: apply 后应再提议, got {proposed_ids}"

        # 找第二次 proposal_id (新 proposed 行, max id)
        rows_all = c.execute(
            "SELECT id FROM audit_log WHERE pass_name='stuck_task' AND ref_id=? AND status='proposed'",
            (tid,),
        ).fetchall()
        pid2 = max(r[0] for r in rows_all)
        assert pid2 != pid1, f"second proposal id 应不同, got {pid2}"

        # 第二次 apply 应被 M28.1 + M30 双重拒绝: ref_id 已 applied, 任何
        # proposal_id 都不能再 apply (即便 proposal_id 本身没被 resolved).
        try:
            task_states.apply_stale_proposal(c, pid2, applied_action="second_apply")
            assert False, "second proposal apply 应抛 ProposalAlreadyResolved"
        except task_states.TaskLoopError as e:
            assert e.code == "ProposalAlreadyResolved"
    finally:
        c.close()


# ===== M30.2 input validation =====

def test_m30_2_propose_rejects_invalid_threshold():
    """[M30.2] propose_stale_tasks 拒绝负数 / 0 / 非 int threshold."""
    _setup()
    c = sqlite3.connect(str(memory.DB_PATH))
    try:
        # 负数
        try:
            task_states.propose_stale_tasks(c, stale_days_threshold=-1)
            assert False, "negative threshold should raise"
        except task_states.TaskLoopError as e:
            assert e.code == "InvalidThreshold"

        # 0
        try:
            task_states.propose_stale_tasks(c, stale_days_threshold=0)
            assert False, "zero threshold should raise"
        except task_states.TaskLoopError as e:
            assert e.code == "InvalidThreshold"

        # 字符串
        try:
            task_states.propose_stale_tasks(c, stale_days_threshold="7")  # type: ignore[arg-type]
            assert False, "string threshold should raise"
        except task_states.TaskLoopError as e:
            assert e.code == "InvalidThreshold"

        # float 仍走 int 校验
        try:
            task_states.propose_stale_tasks(c, stale_days_threshold=7.5)  # type: ignore[arg-type]
            assert False, "float threshold should raise"
        except task_states.TaskLoopError as e:
            assert e.code == "InvalidThreshold"

        # 正整数 OK
        result = task_states.propose_stale_tasks(c, stale_days_threshold=14)
        assert "proposed" in result
    finally:
        c.close()


# ===== M30.3 digest truncation =====

def test_m30_3_render_digest_block4_truncates_to_2000_chars():
    """[M30.3] render_digest_block4 输出截断到 ≤2000 字符 (digest 契约).

    DESIGN §4.4 + README: digest 500-2000 字符. Block 4 (未闭环) 是 digest 一
    部分, 单独应 ≤2000 chars (整体 digest 可能 block 1+2+3+4 一起 ≤2000).
    旧实现不截断 — 大量 stale task 会让 block 4 撑爆 digest, 注入 agent 上下文
    overflow. 修: 截断 + "..." 后缀.
    """
    c = sqlite3.connect(str(memory.DB_PATH))
    try:
        c.execute("PRAGMA foreign_keys = OFF")
        # 清理 m30-digest 残留
        c.execute("DELETE FROM task_states WHERE task_id LIKE 'task:m30-digest-%'")
        c.execute("DELETE FROM entities WHERE id LIKE 'task:m30-digest-%'")
        # 建 100 个超长 name 的 active task
        for i in range(100):
            long_name = "m30-digest-truncate-" + str(i) + "-" + "X" * 50
            tid = "task:m30-digest-" + str(i).zfill(4)
            c.execute(
                "INSERT OR IGNORE INTO entities (id, kind, name, properties_json, valid_until, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (tid, "task", long_name, "{}", "2026-08-06T09:00", "2026-08-06T09:00"),
            )
            c.execute(
                "INSERT OR IGNORE INTO task_states (task_id, state, valid_from, valid_until, reason, evidence_chunk_id) VALUES (?, ?, ?, NULL, ?, NULL)",
                (tid, "open", "2026-08-06T09:00", "task_create"),
            )
        c.commit()

        # 跑 list_active_tasks_and_loops + render_digest_block4
        active = task_states.list_active_tasks_and_loops(c, now="2026-08-06T15:00")
        text_lines, refs = task_states.render_digest_block4(active)
        # render_digest_block4 返回 list[str] — 拼成 string 测长度
        text_block = "\n".join(text_lines)

        # 断言 1: block 4 输出 ≤2000 chars
        n_active = len(active["active_tasks"])
        if n_active > 50:  # 超过 ~50 个 task 应触发截断
            assert len(text_block) <= 2000, "block 4 应 ≤2000 chars, got " + str(len(text_block))
            # 截断后应以 "..." 结尾
            assert text_block.endswith("..."), "截断后应以 ... 结尾"
        else:
            # active_tasks 少, 不应截断
            assert not text_block.endswith("...")
    finally:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("DELETE FROM task_states WHERE task_id LIKE 'task:m30-digest-%'")
        c.execute("DELETE FROM entities WHERE id LIKE 'task:m30-digest-%'")
        c.execute("PRAGMA foreign_keys = ON")
        c.commit()
        c.close()


