"""task_states.py — M2 task/loop 状态机核心 (DESIGN_TASK_LOOP §4.2)."""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("mnelo.task_states")


# === 异常族 ===
class TaskLoopError(Exception):
    def __init__(self, message: str, field: Optional[str] = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code or self.__class__.__name__

    def to_dict(self) -> Dict[str, str]:
        out = {"code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


class TaskNotFoundError(TaskLoopError):
    """task_id 无当前活动窗."""


class InvalidTransitionError(TaskLoopError):
    """from_state → to_state 不在允许图里 (force=False)."""


class NotCurrentStateError(TaskLoopError):
    """CAS 关旧窗 0 行 — 并发 / 重复."""


class EvidenceNotFoundError(TaskLoopError):
    """evidence_chunk_id 提供但 chunks.id 不存在."""


class ReasonRequiredError(TaskLoopError):
    """force=True 需要 reason (D8)."""


class TerminalLoopError(TaskLoopError):
    """cancelled 后再 transfer 拒收."""


# === 状态词汇 ===
TASK_STATES = frozenset({
    "open", "in_progress", "waiting", "blocked", "done", "cancelled",
})
LOOP_STATES = frozenset({"running", "dormant", "paused"})
ALL_STATES = TASK_STATES | LOOP_STATES


def _default_now() -> str:
    return datetime.now().isoformat(timespec="seconds")



def transition(
    conn: Any,
    *,
    task_id: str,
    to_state: str,
    reason: str,
    evidence_chunk_id: Optional[str] = None,
    force: bool = False,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Single CAS transfer (DESIGN §4.2 literal step 1-5).

    Args:
        conn: open sqlite3.Connection (FK on, WAL mode ready).
        task_id: entities.id (kind='task' or kind='loop').
        to_state: 6 task states + 3 loop states.
        reason: required; 含 actor 痕迹 (D8 强制).
        evidence_chunk_id: optional FK to chunks.id.
        force: bypass allowed graph (要求 reason).
        now: optional timestamp override.

    Returns:
        dict 含 window_id, from_state, to_state, valid_from,
        以及 optional 'terminal_bookkeeping' 块.
    """
    # 0. 状态词汇校验
    if to_state not in ALL_STATES:
        raise InvalidTransitionError(
            f"to_state '{to_state}' 不在状态词汇集 "
            f"(task: {sorted(TASK_STATES)}, loop: {sorted(LOOP_STATES)})",
            field="to_state",
        )

    # 0.1 force 必须带 reason (D8)
    if force and not (reason and reason.strip()):
        raise ReasonRequiredError(
            "force=True requires reason (D8 纠正门需要审计痕迹)",
            field="reason",
        )

    # 0.2 evidence_chunk_id 存在性校验
    if evidence_chunk_id is not None:
        row = conn.execute(
            "SELECT 1 FROM chunks WHERE id = ? AND valid_until IS NULL",
            (evidence_chunk_id,),
        ).fetchone()
        if row is None:
            raise EvidenceNotFoundError(
                f"evidence_chunk_id '{evidence_chunk_id}' 不存在或已软删",
                field="evidence_chunk_id",
            )

    # 1. 定位当前活动窗
    cur = conn.execute(
        "SELECT id, state, valid_from FROM task_states "
        "WHERE task_id = ? AND valid_until IS NULL",
        (task_id,),
    ).fetchone()
    if cur is None:
        raise TaskNotFoundError(
            f"task_id '{task_id}' 无活动状态窗 (no open window)",
            field="task_id",
        )
    current_id, from_state, current_valid_from = cur

    # 1.1 cancelled 是 terminal — 任何 transfer 都拒
    if from_state == "cancelled":
        raise TerminalLoopError(
            f"task '{task_id}' 处于 terminal 'cancelled'; 任何 transfer 都拒",
            field="task_id",
        )

    # 1.2 done 的去向: 仅 reopen 逃生门
    if from_state == "done" and to_state != "open" and not force:
        raise InvalidTransitionError(
            f"done 是 terminal (除 reopen 逃生门 done→open): 拒 done→{to_state}",
            field="to_state",
        )

    # 2. 允许图校验
    if not force:
        # 优先查 task 关联的 loop_id (M5 完整), 此处简化: entities.properties_json 里读
        loop_id = conn.execute(
            "SELECT properties_json FROM entities WHERE id = ? AND valid_until IS NULL",
            (task_id,),
        ).fetchone()
        loop_scope = None
        if loop_id and loop_id[0]:
            try:
                props = json.loads(loop_id[0])
                loop_scope = props.get("loop_id")
            except (json.JSONDecodeError, TypeError):
                loop_scope = None

        allowed = conn.execute(
            "SELECT 1 FROM state_transitions "
            "WHERE (scope = ? OR scope = 'default') "
            "  AND from_state = ? AND to_state = ?",
            (loop_scope or "default", from_state, to_state),
        ).fetchone()
        if not allowed:
            raise InvalidTransitionError(
                f"转移 {from_state}→{to_state} 不在允许图里 "
                f"(scope={loop_scope or 'default'})",
                field="to_state",
            )

    # 3. CAS 关旧 + 开新
    ts = now or _default_now()
    affected = conn.execute(
        "UPDATE task_states SET valid_until = ? "
        "WHERE id = ? AND task_id = ? AND valid_until IS NULL",
        (ts, current_id, task_id),
    ).rowcount
    if affected == 0:
        raise NotCurrentStateError(
            f"CAS 关旧窗 0 行 (task_id={task_id}, id={current_id}); "
            f"并发冲突 / 重复提交",
            field="task_id",
        )
    cur2 = conn.execute(
        "INSERT INTO task_states "
        "(task_id, state, valid_from, valid_until, reason, "
        " evidence_chunk_id, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?)",
        (task_id, to_state, ts, reason, evidence_chunk_id, ts),
    )
    new_window_id = cur2.lastrowid

    # 4. 终端簿记 (done/cancelled 且是 loop active_task_id)
    bookkeeping: Optional[Dict[str, Any]] = None
    if to_state in ("done", "cancelled"):
        loops = conn.execute(
            "SELECT id, properties_json FROM entities "
            "WHERE kind = 'loop' AND valid_until IS NULL"
        ).fetchall()
        for loop_id, props_json in loops:
            if not props_json:
                continue
            try:
                props = json.loads(props_json)
            except json.JSONDecodeError:
                continue
            if props.get("active_task_id") == task_id:
                props["active_task_id"] = None
                props["last_cycle_done_at"] = ts
                conn.execute(
                    "UPDATE entities SET properties_json = ? WHERE id = ?",
                    (json.dumps(props), loop_id),
                )
                bookkeeping = {
                    "loop_id": loop_id,
                    "last_cycle_done_at": ts,
                    "action": "clear_active_task",
                }
                logger.info(
                    f"[task_states] 终端簿记: loop {loop_id} "
                    f"active_task_id={task_id} → NULL, last_cycle={ts}"
                )
                break

    result: Dict[str, Any] = {
        "task_id": task_id,
        "from_state": from_state,
        "to_state": to_state,
        "from_valid_from": current_valid_from,
        "valid_from": ts,
        "window_id": new_window_id,
    }
    if bookkeeping:
        result["terminal_bookkeeping"] = bookkeeping
    return result
