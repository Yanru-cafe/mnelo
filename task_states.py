import re
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




class LoopNotFoundError(TaskLoopError):
    """loop_id 不存在或 valid_until 已设."""


def loop_tick(
    conn: Any,
    *,
    loop_id: str,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Mechanically compute loop tick verdict (DESIGN §4.3).

    Args:
        conn: open sqlite3.Connection.
        loop_id: entities.id (kind='loop').
        now: optional timestamp override (default datetime.now local).

    Returns:
        dict {loop_id, verdict, active_task_id?, active_state?, last_cycle_done_at?, interval_hours?, enabled?}.

    Verdict (DESIGN §4.3 step 1-5):
      - dormant:        not enabled
      - waiting:        active_id 存在且 active_state ∉ {done, cancelled}
      - due:            last_cycle_done_at is None (first run) OR
                        elapsed >= interval_hours
      - not_due:        没 active_id 且 last 距今 < interval_hours

    Note: 跟 transition() 不同, loop_tick 不写 task_states — 仅返回 verdict.
    tick 判定不落行 (DESIGN §4.3 strict). 仅生命周期事件 (create/disable/pause)
    落行, M5 完整.
    """
    # 1. 定位 loop entity
    cur = conn.execute(
        "SELECT id, properties_json FROM entities "
        "WHERE id = ? AND kind = 'loop' AND valid_until IS NULL",
        (loop_id,),
    ).fetchone()
    if cur is None:
        raise LoopNotFoundError(
            f"loop_id '{loop_id}' 不存在或已软删",
            field="loop_id",
        )
    _, props_json = cur
    if not props_json:
        raise LoopNotFoundError(
            f"loop '{loop_id}' properties_json 为空",
            field="loop_id",
        )
    try:
        cfg = json.loads(props_json)
    except json.JSONDecodeError as e:
        raise LoopNotFoundError(
            f"loop '{loop_id}' properties_json 解析失败: {e}",
            field="loop_id",
        )

    enabled = bool(cfg.get("enabled", True))
    interval_hours = cfg.get("interval_hours", 24)
    active_task_id = cfg.get("active_task_id")
    last_cycle_done_at = cfg.get("last_cycle_done_at")

    out: Dict[str, Any] = {
        "loop_id": loop_id,
        "enabled": enabled,
        "interval_hours": interval_hours,
        "active_task_id": active_task_id,
        "last_cycle_done_at": last_cycle_done_at,
    }

    # 2. step 1 — not enabled — dormant
    if not enabled:
        out["verdict"] = "dormant"
        return out

    # 3. step 2 — active 在飞 — waiting
    active_state: Optional[str] = None
    if active_task_id:
        active_row = conn.execute(
            "SELECT state FROM task_states "
            "WHERE task_id = ? AND valid_until IS NULL",
            (active_task_id,),
        ).fetchone()
        if active_row is not None:
            active_state = active_row[0]
            if active_state not in ("done", "cancelled"):
                out["verdict"] = "waiting"
                out["active_state"] = active_state
                return out

    # 4. step 3 — last is None — first run
    if last_cycle_done_at is None:
        out["verdict"] = "due"
        if active_state is not None:
            out["active_state"] = active_state
        return out

    # 5. step 4-5 — elapsed vs interval
    try:
        from datetime import datetime as _dt
        last_dt = _dt.fromisoformat(last_cycle_done_at)
        now_dt = _dt.fromisoformat(now) if now else _dt.now()
        elapsed_hours = (now_dt - last_dt).total_seconds() / 3600.0
    except (ValueError, TypeError) as e:
        raise LoopNotFoundError(
            f"loop '{loop_id}' last_cycle_done_at 解析失败: {e}",
            field="loop_id",
        )

    out["elapsed_hours"] = round(elapsed_hours, 4)
    if elapsed_hours < interval_hours:
        out["verdict"] = "not_due"
    else:
        out["verdict"] = "due"
    return out



def list_tasks(
    conn: Any,
    *,
    state: Optional[str] = None,
    loop_id: Optional[str] = None,
    asof: Optional[str] = None,
    stale_days: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    """List task / loop windows (DESIGN §5.1 memory_task_list).

    Args:
        conn: open sqlite3.Connection.
        state: filter by current state (None = active only, 即非 done/cancelled/dormant/paused).
        loop_id: filter by parent loop (从 entities.properties_json 读 loop_id).
        asof: optional timestamp; only valid windows at asof returned.
        stale_days: if True, only windows with valid_from > threshold days ago.
        limit: max rows returned.

    Returns:
        dict {tasks: [{task_id, name, state, state_valid_from, loop_id?, owner_id?,
                       stale_days?}]}.

    Note: 把 list_tasks 放 step 3, 不在 transition() 一起, 是为可独立 ship.
    """
    where = []
    params: List[Any] = []

    # 当前活动窗过滤 (valid_until IS NULL)
    if asof is None:
        where.append("ts.valid_until IS NULL")
    else:
        # asof: 匹配 valid_from <= asof AND (valid_until IS NULL OR valid_until > asof)
        where.append("ts.valid_from <= ?")
        params.append(asof)
        where.append("(ts.valid_until IS NULL OR ts.valid_until > ?)")

    if state is not None:
        if state not in ALL_STATES:
            raise InvalidTransitionError(
                f"state '{state}' 不在状态词汇集",
                field="state",
            )
        where.append("ts.state = ?")
        params.append(state)
    elif asof is None:
        # 默认: 仅 active (排除 done/cancelled/dormant/paused)
        where.append("ts.state NOT IN ('done','cancelled','dormant','paused')")

    if loop_id is not None:
        # properties_json 含有 loop_id 字段 (M3 task_create 写入, M5 完整)
        # 简化: 用 LIKE 匹配 JSON 字符串. 精准方案走 json_extract.
        where.append("(e.properties_json LIKE ?)")
        params.append(f'%"loop_id": "{loop_id}"%')

    sql = (
        "SELECT ts.task_id, e.name, ts.state, ts.valid_from, "
        "       e.properties_json, e.aliases_json "
        "FROM task_states ts JOIN entities e ON e.id = ts.task_id "
        "WHERE e.kind = 'task' AND " + " AND ".join(where) + " "
        "ORDER BY ts.valid_from ASC LIMIT ?"
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    tasks = []
    for r in rows:
        task_id, name, st, vf, props_json, aliases_json = r
        loop_id_val = None
        owner_id_val = None
        if props_json:
            try:
                p = json.loads(props_json)
                loop_id_val = p.get("loop_id")
                owner_id_val = p.get("owner_id")
            except (json.JSONDecodeError, TypeError):
                pass
        entry = {
            "task_id": task_id,
            "name": name,
            "state": st,
            "state_valid_from": vf,
            "loop_id": loop_id_val,
            "owner_id": owner_id_val,
        }
        if stale_days:
            # 算 valid_from 距今多少天 (best-effort, 不严格 to-the-second)
            try:
                vf_dt = datetime.fromisoformat(vf)
                age_days = (datetime.now() - vf_dt).days
            except (ValueError, TypeError):
                age_days = None
            entry["stale_days"] = age_days
        tasks.append(entry)

    return {"tasks": tasks, "count": len(tasks), "truncated": len(tasks) >= limit}


def replay_task(
    conn: Any,
    *,
    task_id: str,
    asof: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay a task's full state-window history (DESIGN §5.1 memory_task_replay).

    Args:
        conn: open sqlite3.Connection.
        task_id: entities.id.
        asof: optional timestamp; if given, only include windows valid at asof.

    Returns:
        dict {task_id, current_state, window_count, windows: [...]}.
    """
    params: List[Any] = [task_id]
    where = "task_id = ?"
    if asof is not None:
        where += " AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)"
        params.extend([asof, asof])

    rows = conn.execute(
        "SELECT state, valid_from, valid_until, reason, evidence_chunk_id "
        "FROM task_states WHERE " + where + " ORDER BY valid_from ASC",
        params,
    ).fetchall()

    cur = conn.execute(
        "SELECT state FROM task_states WHERE task_id=? AND valid_until IS NULL",
        (task_id,),
    ).fetchone()
    current_state = cur[0] if cur else None

    windows = [
        {
            "state": r[0],
            "valid_from": r[1],
            "valid_until": r[2],
            "reason": r[3],
            "evidence_chunk_id": r[4],
        }
        for r in rows
    ]
    return {
        "task_id": task_id,
        "current_state": current_state,
        "window_count": len(windows),
        "windows": windows,
    }



def task_create(
    conn: Any,
    *,
    name: str,
    loop_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    priority: int = 3,
    summary: Optional[str] = None,
    evidence_chunk_id: Optional[str] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a task entity + open state window (DESIGN §5.1 memory_task_create).

    Args:
        conn: open sqlite3.Connection.
        name: human label (e.g. '采购耗材').
        loop_id: optional parent loop; if given, requires loop tick-verdict ready.
        owner_id: optional entity id (default person:yanru).
        priority: 0-5, default 3.
        summary: optional.
        evidence_chunk_id: optional FK to chunks.id.
        now: optional timestamp override.

    Returns:
        dict {task_id, current_state, loop_id?, created_at, open_window_id}.

    Raises:
        InvalidLoopError: loop_id provided but loop not found.
        LoopDisabledError: loop is disabled (enabled=False).
        LoopHasActiveTaskError: loop already has active_task_id (防双 spawn).
        EvidenceNotFoundError: evidence_chunk_id not found.
    """
    if not name or not name.strip():
        raise TaskLoopError("name 必填", field="name", code="InvalidInputError")

    if priority < 0 or priority > 5:
        raise TaskLoopError(
            f"priority {priority} 不在 0-5 范围",
            field="priority",
            code="InvalidInputError",
        )

    # 0. evidence_chunk_id 校验
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

    # 1. loop 校验 (loop_id 提供了)
    if loop_id is not None:
        loop_row = conn.execute(
            "SELECT id, properties_json FROM entities "
            "WHERE id = ? AND kind = 'loop' AND valid_until IS NULL",
            (loop_id,),
        ).fetchone()
        if loop_row is None:
            raise LoopNotFoundError(
                f"loop_id '{loop_id}' 不存在",
                field="loop_id",
            )
        _, props_json = loop_row
        if not props_json:
            raise LoopNotFoundError(
                f"loop '{loop_id}' properties_json 为空",
                field="loop_id",
            )
        try:
            cfg = json.loads(props_json)
        except json.JSONDecodeError as e:
            raise LoopNotFoundError(
                f"loop '{loop_id}' properties_json 解析失败: {e}",
                field="loop_id",
            )
        if not cfg.get("enabled", True):
            raise TaskLoopError(
                f"loop '{loop_id}' 已禁用 (enabled=False)",
                field="loop_id",
                code="LoopDisabledError",
            )
        if cfg.get("active_task_id"):
            raise TaskLoopError(
                f"loop '{loop_id}' 已有 active_task_id={cfg['active_task_id']} "
                f"(防双 spawn, §边界 #8)",
                field="loop_id",
                code="LoopHasActiveTaskError",
            )

    # 2. 生成 task_id (DESIGN §2.1: task:YYYYMMDD-<slug>)
    ts = now or _default_now()
    try:
        date_part = ts[:10].replace("-", "")
    except (TypeError, ValueError):
        date_part = "00000000"
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())[:30].strip("-")
    if not slug:
        slug = "task"
    task_id = f"task:{date_part}-{slug}"

    # 确保 id 唯一 (collide 试 1-2 次)
    n = 0
    while conn.execute("SELECT 1 FROM entities WHERE id = ?", (task_id,)).fetchone():
        n += 1
        task_id = f"task:{date_part}-{slug}-{n}"
        if n > 100:
            raise TaskLoopError(
                f"task_id 撞名 100+ 次: {task_id}",
                field="task_id",
                code="TaskIdCollisionError",
            )

    # 3. INSERT entity (kind=task, memory_type=ephemeral, properties_json={loop_id, owner_id, priority, summary})
    props = {
        "loop_id": loop_id,
        "owner_id": owner_id,
        "priority": priority,
    }
    if summary:
        props["summary"] = summary
    conn.execute(
        "INSERT INTO entities (id, kind, name, summary, properties_json, memory_type) "
        "VALUES (?, ?, ?, ?, ?, 'ephemeral')",
        (task_id, "task", name, summary, json.dumps(props)),
    )

    # 4. INSERT task_states: open 窗
    cur = conn.execute(
        "INSERT INTO task_states "
        "(task_id, state, valid_from, valid_until, reason, evidence_chunk_id, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?)",
        (task_id, "open", ts, "task_create", evidence_chunk_id, ts),
    )
    open_window_id = cur.lastrowid

    # 5. 关联 loop: 写 loop.properties_json.active_task_id
    if loop_id is not None:
        cfg["active_task_id"] = task_id
        conn.execute(
            "UPDATE entities SET properties_json = ? WHERE id = ?",
            (json.dumps(cfg), loop_id),
        )
        # 6. 写 loop 状态窗 'running' (DESIGN §4.3 生命周期事件)
        # 先看 loop 是否有 active 状态窗
        cur_loop_win = conn.execute(
            "SELECT id FROM task_states WHERE task_id = ? AND valid_until IS NULL",
            (loop_id,),
        ).fetchone()
        if cur_loop_win is None:
            conn.execute(
                "INSERT INTO task_states "
                "(task_id, state, valid_from, valid_until, reason, evidence_chunk_id, created_at) "
                "VALUES (?, ?, ?, NULL, ?, NULL, ?)",
                (loop_id, "running", ts, "loop: spawn task", ts),
            )
        # else: 已是 running 不重复落行

    result: Dict[str, Any] = {
        "task_id": task_id,
        "current_state": "open",
        "created_at": ts,
        "open_window_id": open_window_id,
    }
    if loop_id:
        result["loop_id"] = loop_id
    return result


def loop_create(
    conn: Any,
    *,
    name: str,
    trigger: str,
    interval_hours: int = 24,
    enabled: bool = True,
    priority: int = 3,
    owner_id: Optional[str] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a loop entity (DESIGN §5.1 memory_loop_create)."""
    if not name or not name.strip():
        raise TaskLoopError("name 必填", field="name", code="InvalidInputError")
    if not trigger or not trigger.strip():
        raise TaskLoopError("trigger 必填", field="trigger", code="InvalidInputError")
    if interval_hours <= 0:
        raise TaskLoopError(
            f"interval_hours {interval_hours} 必须 > 0",
            field="interval_hours",
        )

    ts = now or _default_now()
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())[:30].strip("-") or "loop"
    loop_id = f"loop:{slug}"
    n = 0
    while conn.execute("SELECT 1 FROM entities WHERE id = ?", (loop_id,)).fetchone():
        n += 1
        loop_id = f"loop:{slug}-{n}"
        if n > 100:
            raise TaskLoopError(
                f"loop_id 撞名 100+ 次: {loop_id}",
                field="loop_id",
                code="LoopIdCollisionError",
            )

    props = {
        "trigger": trigger,
        "interval_hours": interval_hours,
        "enabled": enabled,
        "active_task_id": None,
        "last_cycle_done_at": None,
        "priority": priority,
        "owner_id": owner_id,
    }
    conn.execute(
        "INSERT INTO entities (id, kind, name, properties_json, memory_type) "
        "VALUES (?, ?, ?, ?, 'ephemeral')",
        (loop_id, "loop", name, json.dumps(props)),
    )
    # loop 初始状态: dormant (enabled=False) 或 不落窗 (默认 enabled=True; 等第一个 tick)
    if not enabled:
        conn.execute(
            "INSERT INTO task_states "
            "(task_id, state, valid_from, valid_until, reason, evidence_chunk_id, created_at) "
            "VALUES (?, ?, ?, NULL, ?, NULL, ?)",
            (loop_id, "dormant", ts, "create disabled", ts),
        )
    return {"loop_id": loop_id, "enabled": enabled, "interval_hours": interval_hours}
