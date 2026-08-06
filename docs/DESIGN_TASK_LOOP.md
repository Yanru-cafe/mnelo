# 设计 v0.2（细化）：任务/循环状态机（Temporal Task/Loop State Machine）

> **状态**：设计文档，**未实现**。本文件只做设计，不含代码。
> **配套**：DESIGN.md / SCHEMA.md / ARCHITECTURE.md / RUNBOOK.md
> **动机来源**：X 帖子架构启发（2026-08-06 记录，燕如认可）——agency = 入口精良 + 符号可靠接地 + 持久状态机跨时间存在；**状态机绝不可能放上下文里**。mnelo 的 SQLite 时间属性（timestamp / valid_until / asof 回放）就是跨时间状态机的落地载体。
> **v0.2 变更**：在 v0.1 架构基础上细化到可实施粒度——最终 DDL、实体/关系约定、状态机语义（并发/幂等/终端簿记）、loop 引擎算法、API 契约（inputSchema/返回/错误）、停滞策略、边界案例表、测试计划、新增默认决策 D6-D11。

---

## 0. 为什么：状态机必须在上下文之外

**现状缺陷**：对话里的多步未闭环链条（采购耗材：库存不足→下单→发货→收货→更新库存→再低于阈值）只存在于上下文窗口。会话一关链条就断，下次会话重头再来。这不是记忆问题，是**状态机放错了位置**。

**设计目标**：mnelo 成为一堆未闭合事务的**外部大脑**——

1. **符号可靠接地**：链条每一步都有证据 chunk 指回原文，不靠上下文里"我记得"
2. **状态跨时间持续存在**：状态机以时间窗行的形式活在 SQLite 里，asof 可回放
3. **会话只拿投影**：agent 每次只 hydrate「当前有哪些 open 任务 + 各自状态」，绝不在上下文里缓存整条链

---

## 1. 核心模型：三层

```
loop（循环定义, entity kind=loop） —— 可重复的多步过程定义 + 触发规则 + 周期
  └─ spawns ─> task（单次实例, entity kind=task） —— 一次未闭合链条
                 └─ 生命周期 = task_states 状态窗序列（当前状态 = valid_until IS NULL）
```

- **loop**：持久过程，如"耗材库存监控"。携带 trigger / interval / enabled / active_task_id。
- **task**：loop 的一次具体实例。identity 是 entity（kind=task），参与图谱。
- **state**：task 的瞬时状态，是一个**时间窗**，append-only。状态机的"现在" = `valid_until IS NULL` 的那一行；"历史" = 全部行按 valid_from 排序。**SQLite 即状态机**。

**关键：状态不是覆盖写入，是追加。** 每一次转移 = 关旧窗（valid_until=now）+ 开新窗（valid_from=now）。历史永不丢失，永远可回放。

---

## 2. 实体与关系约定

### 2.1 命名

| 项 | 约定 | 示例 |
|---|---|---|
| task entity id | `task:YYYYMMDD-<slug>` | `task:20260806-restock-1` |
| loop entity id | `loop:<slug>` | `loop:consumables-stock` |
| kind | `task` / `loop`（entities.kind 已预留 `task`） | |
| memory_type | `ephemeral`（语义上是一次性工作状态） | |
| aliases_json | 如 `["采购耗材", "restock"]` | |

### 2.2 关系命名（graph 一等公民）

| relation | source → target | 语义 |
|---|---|---|
| `part_of` | task → loop | task 是 loop 的一次迭代 |
| `owned_by` | task → person | 责任人（默认 `person:yanru`） |
| `references` | task → chunk | 可选，指向核心证据 chunk |

`task_states.evidence_chunk_id` 已是逐窗接地的硬指针；`references` 关系是可选的图谱便利边。

---

## 3. Schema（最终 DDL）

### 3.1 `task_states`（状态机本体）

```sql
CREATE TABLE task_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,              -- entities.id (kind=task 或 loop)
    state TEXT NOT NULL CHECK (state IN (
        'open','in_progress','waiting','blocked','done','cancelled',  -- task
        'running','dormant','paused'                                  -- loop
    )),
    valid_from TEXT NOT NULL,           -- 进入该状态时刻, memory.now() (T 分隔)
    valid_until TEXT,                   -- 离开时刻, NULL = 当前状态
    reason TEXT,                        -- 语义摘要: 为什么进入该状态
    evidence_chunk_id TEXT,             -- 接地: 支撑这次转移的 chunk (推荐必填, 创建时可空)
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES entities(id),
    FOREIGN KEY (evidence_chunk_id) REFERENCES chunks(id)
);

-- 不变量 1: 同一 task 同时最多一个当前状态行 (状态机不可能双开)
CREATE UNIQUE INDEX ux_task_current_state
    ON task_states(task_id) WHERE valid_until IS NULL;

-- 查询"当前所有活跃任务" (join entities 过滤 kind='task')
CREATE INDEX idx_task_states_open
    ON task_states(state) WHERE valid_until IS NULL AND state NOT IN ('done','cancelled','dormant','paused');

-- asof 回放 (按 task 取全部窗)
CREATE INDEX idx_task_states_task_valid
    ON task_states(task_id, valid_from, valid_until);
```

**参考查询**：

```sql
-- 当前状态
SELECT * FROM task_states WHERE task_id = ? AND valid_until IS NULL;

-- 当前所有活跃任务 (排除 loop 自身的状态行)
SELECT ts.*, e.name FROM task_states ts
  JOIN entities e ON e.id = ts.task_id
 WHERE ts.valid_until IS NULL
   AND ts.state NOT IN ('done','cancelled')
   AND e.kind = 'task'
 ORDER BY ts.valid_from;

-- asof 时点状态 (某时刻该 task 处于什么状态)
SELECT * FROM task_states
 WHERE task_id = ? AND valid_from <= :asof
   AND (valid_until IS NULL OR valid_until > :asof)
 ORDER BY valid_from;
```

**事务顺序（转移的原子性）**：`BEGIN IMMEDIATE` → `UPDATE` 关旧窗 → `INSERT` 开新窗 → `COMMIT`。`ux_task_current_state` 在提交点校验；`UPDATE` 影响 0 行 = 并发冲突/重复提交 → 报 `NOT_CURRENT_STATE` 中止（CAS 语义）。

### 3.2 `state_transitions`（允许转移图，全局默认 + loop 可覆盖）

```sql
CREATE TABLE state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'default',   -- 'default' 全局 / 具体 loop_id 覆盖
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    UNIQUE(scope, from_state, to_state)
);
```

**默认转移矩阵（seed，scope='default'）**：

| from \ to | open | in_progress | waiting | blocked | done | cancelled |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **open** | — | ✅ | — | — | ✅ | ✅ |
| **in_progress** | — | — | ✅ | ✅ | ✅ | ✅ |
| **waiting** | — | ✅ | — | — | ✅ | ✅ |
| **blocked** | — | ✅ | ✅ | — | ✅ | ✅ |
| **done** | ✅ (reopen 逃生门) | — | — | — | — | — |
| **cancelled** | — | — | — | — | — | — (terminal) |

loop 状态（running/dormant/paused）不在此表——loop 的状态由 `loop_tick` 机械判定，仅记录生命周期事件（§4.3）。

### 3.3 `loop` 配置（entities.properties_json 完整 schema）

```json
{
  "trigger": "耗材库存低于阈值",          // 语义条件描述 (agent 评估, 非可执行)
  "interval_hours": 24,                  // recheck 周期
  "enabled": true,                       // false = dormant
  "active_task_id": "task:20260806-restock-1",  // 当前在飞实例; NULL = 无
  "last_cycle_done_at": "2026-08-05T18:00:00",  // 上次闭环时刻; NULL = 从未跑过
  "priority": 3                          // 0-5, 供 digest 排序
}
```

### 3.4 落库路径

- **双写**：`schema.sql`（全新安装，init_db.py）与 memory.py `_migrate_schema()`（存量库，idempotent `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`）。H-1 教训：两边不一致出 bug。
- **seed**：`state_transitions` 默认矩阵在双写后 seed（`INSERT OR IGNORE`，idempotent）。
- **不参与向量**：task_states / state_transitions 是结构化状态，不 embed。

---

## 4. 状态机语义

### 4.1 状态词汇

- **task**：`open`（未闭环待推进）/ `in_progress`（推进中）/ `waiting`（等外部事件）/ `blocked`（受阻待决策）/ `done`（闭环）/ `cancelled`（放弃）
- **loop**：`running`（有在飞实例）/ `dormant`（未启用或等待期）/ `paused`（手动暂停）——**tick 判定不落行**，仅生命周期事件（create/disable/enable/pause/resume）记状态窗。

### 4.2 转移函数（契约）

```
transition(task_id, to_state, reason, evidence_chunk_id=None, force=False)

1. 定位当前窗: SELECT * FROM task_states WHERE task_id=? AND valid_until IS NULL
   无 → TASK_NOT_FOUND（无 open 窗）
2. 允许图校验 (force=False 时):
   SELECT 1 FROM state_transitions
    WHERE scope IN ('default', <task.loop_id>) AND from_state=当前state AND to_state=?
   无 → INVALID_TRANSITION
   force=True 时: 必须带 reason，绕过允许图（逃生门，用于纠正）
3. 证据校验 (推荐必填): evidence_chunk_id 存在且 valid_until IS NULL
   提供但不存在 → EVIDENCE_NOT_FOUND
4. CAS 事务 (BEGIN IMMEDIATE):
   n = UPDATE task_states SET valid_until=now
       WHERE task_id=? AND valid_until IS NULL AND id=当前窗id
   n==0 → NOT_CURRENT_STATE（并发冲突/重复提交）
   INSERT 新窗 (task_id, state=to_state, valid_from=now, reason, evidence_chunk_id)
5. 终端簿记 (to_state ∈ {done, cancelled} 且是 loop 的 active_task_id 时):
   UPDATE loop properties_json:
     active_task_id=NULL, last_cycle_done_at=now   ← 机械簿记, 非决策
   COMMIT
```

**幂等/并发**：重复提交同一 `to_state` → 第 2 次 CAS 关窗 0 行 → 报错而非静默成功。两个 agent 同时转移 → 一个成功一个 `NOT_CURRENT_STATE`。

### 4.3 loop 引擎

**`loop_tick(loop_id, now=None)` 是机械的、deterministic 的**：

```
loop = entities WHERE id=? AND kind='loop' AND valid_until IS NULL   # 无 → LOOP_NOT_FOUND
cfg = loop.properties_json
1.  not cfg.enabled            → verdict=dormant
2.  active_id = cfg.active_task_id
    if active_id:
        active_state = 当前状态(active_id)
        if active_state ∉ {done, cancelled} → verdict=waiting (不重复 spawn)
3.  last = cfg.last_cycle_done_at
    if last is None             → verdict=due   (首轮)
4.  elapsed(last,now) < interval_hours → verdict=not_due
5.  else                        → verdict=due
```

**分工**：mnelo 只负责状态与节拍，**不评估语义条件**（"库存够不够"是 agent recall + 用户确认的事）。mnelo = 状态库 + 节拍器；agent = 语义执行器。

**spawn 流程（agent 侧）**：

```
loop_tick → due
  → agent 评估 trigger（recall 触发上下文 + 用户确认）
  → memory_task_create(name, loop_id, evidence_chunk_id=触发chunk)
      · 建 task entity (kind=task, memory_type=ephemeral, 豁免 TTL)
      · 开 task_states open 窗
      · loop cfg.active_task_id = 新 task；loop 状态窗 running
      · relate: task --part_of--> loop, task --owned_by--> person:yanru
  → 每步动作 transition(..., evidence_chunk_id)
  → 终端 transition(done/cancelled) 自动清 loop.active_task_id + 记 last_cycle_done_at
  → 再 loop_tick → due? → spawn 下一实例（即「再低于阈值」）
```

### 4.4 停滞检测（stale）

阈值（config.toml `[tasks]`，可调）：

| 状态 | 阈值 | 语义 |
|---|---|---|
| open | > 7d 无转移 | 未推进 |
| waiting | > 14d | 可能被卡住 |
| blocked | > 3d | 需决策 |

查询：`WHERE valid_until IS NULL AND state=:s AND valid_from < now - threshold`。

**上浮方式**：digest「未闭环」块标注 `⚠ 需决策`；停滞候选可挂 L2 hygiene 的 audit_log **Proposal** 模式（只提议不 applied）。**mnelo 绝不自动转移任务**——自动推进 = 幻觉风险，闭环必须经 agent/用户显式确认。

---

## 5. API 契约

### 5.1 MCP tools（mcp_server.py 新增 8 个）

**task 组**

`memory_task_create`
```
input: name(str, req), loop_id(str?), owner_id(str?=person:yanru),
       summary(str?), priority(int 0-5=3), deadline(str?),
       evidence_chunk_id(str?), source(str='claude-code')
return: {task_id, current_state:'open', loop_id?, created_at}
error:  INVALID_LOOP / LOOP_DISABLED / LOOP_HAS_ACTIVE_TASK / EVIDENCE_NOT_FOUND
```
> LOOP_HAS_ACTIVE_TASK：`loop_tick` 已判 waiting，仍强建 → 拒绝（防双 spawn）。

`memory_task_transition`
```
input: task_id(str, req), to_state(str, req, enum), reason(str, req),
       evidence_chunk_id(str?), force(bool=false)
return: {task_id, from_state, to_state, valid_from, valid_until, window_id}
error:  TASK_NOT_FOUND / INVALID_TRANSITION / NOT_CURRENT_STATE / EVIDENCE_NOT_FOUND / REASON_REQUIRED(force)
```

`memory_task_list`
```
input: state(str? enum, 默认=全部活跃), loop_id(str?), asof(str?),
       stale_days(bool=false), limit(int=50)
return: {tasks: [{task_id, name, state, state_valid_from, stale_days?, loop_id, owner_id}]}
```

`memory_task_replay`
```
input: task_id(str, req), asof(str?)      # asof 省略=全史
return: {task_id, current_state,
         windows: [{state, valid_from, valid_until, reason, evidence_chunk_id}],
         window_count}
```

**loop 组**

`memory_loop_create`
```
input: name(str, req), trigger(str, req), interval_hours(int=24),
       enabled(bool=true), priority(int=3), owner_id(str?=person:yanru)
return: {loop_id}
```

`memory_loop_update`
```
input: loop_id(str, req), enabled(bool?), interval_hours(int?),
       trigger(str?), priority(int?), pause(bool?)
return: {loop_id, enabled, interval_hours, active_task_id}
```

`memory_loop_tick`
```
input: loop_id(str, req), now(str?)
return: {loop_id, verdict: due|waiting|dormant|not_due,
         active_task_id?, active_state?, last_cycle_done_at?}
error:  LOOP_NOT_FOUND
```

`memory_loop_list`
```
input: enabled_only(bool=false)
return: {loops: [{loop_id, name, trigger, interval_hours, enabled,
                  active_task_id?, active_state?, verdict}]}
```

**契约风格**：inputSchema 沿用 memory_remember 等现有定义（properties + required + enum + default）；错误走现有 ValidationError 带 field 的路径。

### 5.2 CLI（scripts/task_manager.py，仿 identity_fact_manager 模式）

```
task create   --name "采购耗材" --loop loop:consumables [--owner person:yanru --evidence chunk:...]
task transition --id task:... --to done --reason "已收货" [--evidence chunk:... --force]
task list     [--state open --loop loop:consumables --stale --json]
task replay   --id task:... [--asof 2026-08-01T12:00:00 --json]
loop create   --name "耗材库存" --trigger "库存低于阈值" --interval 24
loop update   --id loop:... --enabled false
loop tick     --id loop:... [--json]
loop list     [--json]
```
约定：`--json` 机器输出；退出码 0 成功 / 1 错误 / 2 未找到或取消（沿用 identity_fact_manager）。

### 5.3 client（api/mnelo_client.py 新增）

`create_task()` / `transition_task()` / `list_tasks()` / `replay_task()` / `create_loop()` / `update_loop()` / `tick_loop()` / `list_loops()`——各为对应 MCP tool 的薄封装（同现有 `remember/recall/relate` 风格）。

### 5.4 digest 集成（session_start_digest.py）

「未闭环」块追加在近期决策之后：

```
未闭环 (2 条):
  [task:20260806-restock-1] waiting · 3d · 耗材采购（已下单，等物流）
  [task:20260805-vendor-approval] blocked · 5d ⚠ 需决策
```

实现：`memory_task_list(state=None, stale_days=true, limit=10)`，截断时 `truncated` 标记。每次会话开场，agent 一眼看到需要跟进的链条。

### 5.5 recall 集成

- entity recall 命中 kind=task 实体 → 附 `current_state` + `stale_days`
- vector recall 命中某 chunk 是活跃 task 的 `evidence_chunk_id` → 附该 task 状态（子查询补注）

---

## 6. 边界案例

| # | 场景 | 处理 |
|---|---|---|
| 1 | `done` → `open`（reopen） | 允许（逃生门），需 reason，开新窗 |
| 2 | `cancelled` 再转移 | terminal，拒绝（INVALID_TRANSITION） |
| 3 | 同一 task 并发转移 | CAS 关窗 0 行 → 后到者 NOT_CURRENT_STATE |
| 4 | 证据 chunk 被软删 | 状态窗保留（evidence 是快照指针，软删不破坏状态机）；FK 可空 |
| 5 | loop 被 disable 时在飞 task | 在飞 task 不受影响继续；loop_tick → dormant |
| 6 | loop 被 forget（软删） | task 的 `part_of` 边随级联失效；task 变孤儿但可查询/推进 |
| 7 | 对不存在 loop 建 task | INVALID_LOOP |
| 8 | loop_tick 判 waiting 仍强建 | LOOP_HAS_ACTIVE_TASK 拒绝（防双 spawn 竞态） |
| 9 | 多 task 引用同一证据 chunk | 允许（证据可被多链条共享） |
| 10 | asof 回放到 done 之前 | 返回当时的状态窗（valid_from <= asof < valid_until） |
| 11 | task 无 loop（独立一次性） | 允许：loop_id 为空，终端不触发 loop 簿记 |

---

## 7. 与现有机制的关系

- entities.kind 已预留 `task`；task/loop 是 graph 一等公民
- **TTL 豁免**：L2 hygiene 的 TTL/decay pass **必须排除 kind IN ('task','loop')**——task 的生命周期由 task_states 管理，entity 级 TTL 会误杀跨月的长任务。这是 v0.2 明确的硬规则（见 D11）。
- **软删除**：task 放弃 = `cancelled` 状态窗，不是删实体；实体级 forget 仍可用（边界 #6）
- **L2 hygiene**：停滞上浮复用 audit_log Proposal 模式（只提议不 applied）；新 pass 名 `stuck_task`
- **asof 回放**：复用现有 asof 切片同一思路（`valid_from <= asof < valid_until`）
- **audit_log**：task_states 转移天然 append-only，无需额外审计；loop 簿记的 UPDATE 走正常写路径

---

## 8. 推进机制（谁推动状态机）

状态机在库里，推动者在外部。三种驱动按需组合：

1. **agent 驱动**（主）：会话/定时触发时 agent 查 due loops → 执行语义动作 → transition 记状态
2. **cron/timer 驱动**（二期）：loop interval 到期 → cc-connect cron/timer 唤醒 agent 去 tick
3. **事件驱动**：外部事件（物流单号、传感器值）作为 chunk 写入 → agent 判定是否转移

**原则：mnelo 绝不自主转移**（避免幻觉推进），只"提醒 + 记录"。闭环必须经过 agent 或用户显式确认。

---

## 9. 演练：采购耗材 loop（office-lady）

```
1. 会话记「耗材库存不足」→ chunk 落库
2. loop tick loop:consumables → due
3. agent 评估 trigger → memory_task_create(name="采购耗材", loop_id=loop:consumables,
                                          evidence_chunk_id=chunk_触发)
     · task entity  +  open 状态窗  +  loop.active_task_id=task:20260806-restock-1
     · relate: task --part_of--> loop, task --owned_by--> person:yanru
4. 下单 → transition(in_progress, reason="已下单", evidence=chunk_订单)
5. 物流消息进来 → chunk 记住 → transition(waiting, reason="等发货", evidence=chunk_物流)
6. 收货确认 → transition(done, reason="已收货", evidence=chunk_收货)
     · 终端簿记: loop.active_task_id=NULL, last_cycle_done_at=now
7. loop tick loop:consumables → 仍低于阈值 → due → spawn task:...-restock-2 (open)
8. 任意时点 task replay task:...-restock-1 → 完整生命周期; digest 显示当前 open 的 restock-2
```

这条链从头到尾，agent 上下文里只有每步的投影，**状态机全在库里**。

---

## 10. 决策

### 10.1 v0.1 已拍板（2026-08-06 燕如确认）

| # | 决策 | 定案 |
|---|---|---|
| 1 | 状态词汇粒度 | ✅ 建议集：open/in_progress/waiting/blocked/done/cancelled |
| 2 | loop 形态 | ✅ 一等公民实体（trigger/interval/active_task_id/enabled） |
| 3 | 推动机制首期 | ✅ 先 agent 驱动；cron/timer 二期 |
| 4 | 证据要求 | ✅ 推荐必填（创建可跳过，转移带 evidence_chunk_id） |
| 5 | 停滞处理 | ✅ 只上浮不自动推进（mnelo 绝不自主转移） |

### 10.2 v0.2 新增默认（默认采用，燕如可否决）

| # | 决策 | 默认 |
|---|---|---|
| D6 | 每 loop 同时在飞实例数 | **1 个**（active_task_id 单值）；并行实例二期 |
| D7 | 停滞阈值 | open >7d / waiting >14d / blocked >3d（config `[tasks]` 可调） |
| D8 | transition `force` 逃生门 | 需 reason，绕过允许图，默认 false（用于纠正） |
| D9 | 终端自动簿记 | done/cancelled 自动清 active_task_id + 记 last_cycle_done_at（机械簿记，非决策） |
| D10 | loop 状态窗 | 仅生命周期事件（create/disable/enable/pause）落行；tick 判定不落行 |
| D11 | TTL 豁免 | kind=task/loop **排除**出 L2 TTL/decay pass |
| D12 | 工具收敛 | 首期 8 个新工具（4 task + 4 loop），不拆更细 |

---

## 11. 测试计划（M1/M2 映射，沿用项目 pytest 风格）

**M1 — schema**
- `test_task_states_table_created`（全新 + 迁移双写）
- `test_ux_task_current_state_rejects_double_open`（不变量 1）
- `test_state_transitions_seeded_defaults`（seed 矩阵存在）
- `test_task_loop_kind_in_entities`（kind 校验）

**M2 — 行为**
- `test_transition_ok_closes_old_opens_new`
- `test_transition_invalid_graph_rejected` / `test_transition_force_requires_reason`
- `test_transition_evidence_missing_rejected`
- `test_transition_reopen_done_to_open` / `test_transition_terminal_cancelled_rejected`
- `test_transition_concurrent_double_submit_fails`（CAS）
- `test_loop_tick_due_first_run` / `not_due` / `waiting_active` / `dormant_disabled`
- `test_loop_spawn_sets_active_task` / `test_terminal_clears_active_task_sets_last_cycle`
- `test_replay_full_history` / `test_replay_asof_point`
- `test_stale_query_returns_overdue`

---

## 12. 落地里程碑（分期，不在本期写代码）

- **M1**：schema（task_states + state_transitions + 双写 migrate + seed + 不变量测试）
- **M2**：核心行为（transition CAS / list / replay / loop_tick / 终端簿记 + 校验）
- **M3**：API 面（8 个 MCP tools + task_manager CLI + client 薄封装）
- **M4**：digest「未闭环」块 + recall 状态补注 + stale 上浮
- **M5**：cron/timer 定时 tick（D3 二期）+ L2 `stuck_task` Proposal
