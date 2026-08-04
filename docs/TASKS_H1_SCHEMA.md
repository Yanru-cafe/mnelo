# H-1 schema 设计（3 项前置：user_confirmed / processed_at / audit_log）

> **来源**：hermes 设计草案 + deepseek-v4-flash cross-check（2026-08-04）。
> **背景**：8/4 实战数据评估 + v0.12 DESIGN 落地后，TASKS_L2_HYGIENE H0（audit_log + Proposal/Policy/Applier 基建）之前必须建的 3 个 schema 前置（v0.12 §1.1.1 表）。
> **状态**：草案已评审，deepseek cross-check 结论见 §0；**schema-only，不含业务逻辑**（H0 才引入 Proposal/Policy/Applier + 工具）。
> **配套文档**：DESIGN v0.12（§1.1.1/§1.2.1）、TASKS_L2_HYGIENE v0.2（H0-H8）、v0.3 实战数据报告。

---

## 0. deepseek-v4-flash cross-check 结论（8/4）

> 对 hermes 草案的 7 个问题逐条 verdict + 3 个草案未问到的真问题。

### 0.1 7 个问题 verdict

| # | 问题 | deepseek verdict | 说明 |
|---|---|---|---|
| 1 | user_confirmed NOT NULL DEFAULT 0 vs 可空 | ✅ **同意 NOT NULL DEFAULT 0** | NULL 引入第三态，`WHERE user_confirmed=1` 与"排除保护项"都要处理 `IS NULL OR`；§3.7 保护是二元的，二态足够 |
| 2 | processed_at 双表 vs 只 chunks | ✅ **同意双表，relations 不需要** | 卫生 pass 只对 chunks/entities 做衰减/TTL（两者有 importance + memory_type）；**relations 无 importance/memory_type**，不参与；将来需要可 idempotent ALTER 再加 |
| 3 | audit_log UNIQUE 含 status | ✅ **同意 status 进 UNIQUE，但补一个边界测试** | 正常状态机 OK（四 status 不同→允许多行，同 run 同 status 重复 apply 被拦）。**边界**：§5.9.1 "reverted 可再次 apply"——若 re-apply 在**同一 run_id 内**会插第二条 `applied` 撞 UNIQUE。缓解：re-apply 用**新 run_id**（设计已如此）；验收加测试（见 §5.2） |
| 4 | audit_log 单表 vs 分表 | ✅ **同意单表 + status** | 分表让状态迁移变"搬行"，破坏 append-only；单表 + 索引最干净 |
| 5 | revert_sql 字段 vs after_json 反推 | ✅ **同意显式 revert_sql** | 反推要每 action_type 写逆向逻辑，脆且难测；apply 时生成 revert_sql 存起来。注意：**内部生成、参数化、别拼接用户输入**（防引号注入） |
| 6 | master 实体不自动 init | ✅ **同意** | 符合 §1.4 显式选择。隐含事实：在主人设 user_confirmed=1 前，§3.7 "master 100% 不可变"**空转**（所有实体可被 L2 改）——可接受，文档应明说"保护未激活直到主人确认" |
| 7 | H-1 是否含 mcp_server API | ✅ **同意不含，schema-only** | 拆 commit 方便回滚；user_confirmed 参数随 H0 或单独小 commit。schema-only 阶段该列是"死的"（无 API 写）——对前置任务正确 |

### 0.2 3 个草案未问到的真问题

| # | 问题 | 修正 |
|---|---|---|
| **A** | **草案只展示 `_migrate_schema()`，`schema.sql` 也要同步改** | `_migrate_schema()` 管存量库，`schema.sql` 管全新安装（init_db.py）。**两边都加**（3 列 + audit_log 表 + 索引），否则全新机器装出来缺列、又被 _migrate_schema 补——不一致 |
| **B** | **时间戳格式不统一**（存量问题，草案在延续） | 草案 `audit_log.created_at DEFAULT datetime('now','localtime')` → 空格分隔 `2026-08-04 19:12:00`；memory.py `now()` → T 分隔 `2026-08-04T19:12:00`。库里已混两种。**audit_log 内部统一**（全用 SQLite 默认 或 全用 Python now()），否则 created_at 排序/比较踩坑 |
| **C** | **user_confirmed 索引选择性低**（minor） | 默认 0、绝大多数行 0，普通 B-tree 选择性差。§3.7 查询是"找 user_confirmed=1 排除保护项"→ **用 partial index `WHERE user_confirmed = 1`** |

---

## 1. `entities.user_confirmed` 列

### 1.1 设计（已采纳 cross-check）

```sql
ALTER TABLE entities ADD COLUMN user_confirmed INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_entities_user_confirmed ON entities(user_confirmed) WHERE user_confirmed = 1;  -- [C] partial index
```

### 1.2 关键设计点

| 决策 | 定案 | 理由 |
|---|---|---|
| 类型 | INTEGER 0/1 | SQLite 无原生 BOOL；0=未确认, 1=确认 |
| 默认值 | **NOT NULL DEFAULT 0** | 默认不护，显式选择（Q1 verdict） |
| 索引 | **partial index `WHERE user_confirmed = 1`**（C） | 查"找保护项排除"；避免低选择性全索引 |
| master 用户实体 | 实战 0 个；**不自动 init，等主人手动**（Q6 verdict） | 保护未激活直到主人确认；§1.4 显式选择 |
| API 影响 | **H-1 阶段不加**（schema-only，Q7 verdict）；H0 随 memory_remember 加 `user_confirmed: Optional[bool]=None` | 拆 commit 方便回滚 |
| migration 顺序 | 1/3 第一个建 | idempotent ALTER（f1bc1bf 模式） |
| trigger | 无 | 只是 §3.7 L2 查询过滤条件 |

### 1.3 实战影响
- 4498 entities 全获 user_confirmed=0；主人确认的实体可 set 1
- 14 天无显式确认——L2 H3/H4 默认不会误伤（全部可被 L2 改，直到主人确认）

---

## 2. `chunks/entities.processed_at` 列（双表对称）

### 2.1 设计（已采纳 cross-check）

```sql
ALTER TABLE chunks ADD COLUMN processed_at TEXT;
ALTER TABLE entities ADD COLUMN processed_at TEXT;
CREATE INDEX idx_chunks_processed_at ON chunks(processed_at);
CREATE INDEX idx_entities_processed_at ON entities(processed_at);
```

### 2.2 关键设计点

| 决策 | 定案 | 理由 |
|---|---|---|
| 类型 | TEXT ISO 8601 | 与现有 timestamp/valid_* 一致 |
| NULL 语义 | **NULL = 未跑过 L2**；NOT NULL = 处理时刻 | TASKS H5 watermark 直接 `WHERE processed_at IS NULL` 选候选 |
| 表范围 | **chunks + entities 双表，relations 不加**（Q2 verdict） | relations 无 importance/memory_type，不参与衰减/TTL |
| 索引 | 普通 B-tree 单列 | watermark 查询走索引 |
| 与 last_recalled 区别 | processed_at = L2 处理；last_recalled = 召回 | 独立维度，不合并 |
| API 影响 | 无直接 API（L2 内部维护）；memory_stats 扩展显示 `processed_at IS NULL count` | 工具收敛（不新加工具） |
| watermark 存储 | `meta` 表（`l2.last_run.hygiene`），不新建 l2_watermark 表 | 单一事实源 |
| trigger | 无 | watermark 是 L2 pass 显式维护 |

### 2.3 实战影响
- 4344 chunks + 4498 entities 全获 processed_at=NULL（L2 未落地，语义一致）
- H0/H5 落地后 L2 可增量跑（按 processed_at 排序）

---

## 3. `audit_log` 表（H0 核心）

### 3.1 设计（已采纳 cross-check）

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,              -- UUID 标识一次 L2 pass run
    pass_name TEXT NOT NULL,            -- 'contradict' / 'dedup' / 'hygiene' / ...
    action_type TEXT NOT NULL,          -- 'decay_importance' / 'ttl_expire' / ...
    ref_type TEXT NOT NULL,             -- 'chunk' / 'entity' / 'relation'
    ref_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    confidence REAL DEFAULT 1.0,
    llm_used INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'proposed',  -- 'proposed'/'applied'/'skipped'/'reverted'
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    revert_sql TEXT,
    UNIQUE(run_id, pass_name, action_type, ref_id, status)
);
CREATE INDEX idx_audit_log_run ON audit_log(run_id);
CREATE INDEX idx_audit_log_pass ON audit_log(pass_name, status);
CREATE INDEX idx_audit_log_ref ON audit_log(ref_type, ref_id);
CREATE INDEX idx_audit_log_created ON audit_log(created_at);
```

### 3.2 关键设计点

| 决策 | 定案 | 理由 |
|---|---|---|
| 状态机 | `proposed` → `applied`/`skipped`/`reverted`（append-only 多行） | §5.9.1；多行 = 一 ref 可有 proposed+applied+reverted 多行 |
| UNIQUE | `(run_id, pass_name, action_type, ref_id, status)` | 防同 run 重复 apply；**注意 re-apply 边界**（Q3 verdict，见 §5.2 测试） |
| before/after | TEXT JSON 字符串 | 灵活；§5.6 "全记录" |
| revert_sql | TEXT 反向 SQL，**仅 applied 有** | §5.6 撤销重放；**内部生成、参数化、不拼接用户输入**（Q5 verdict） |
| created_at 格式 | **统一**（B 修正，见下） | 与 memory.py `now()` 格式一致，避免混格式 |
| 单表 vs 分表 | **单表 + status**（Q4 verdict） | append-only 不被"搬行"破坏 |
| 与 recall_log 区别 | recall_log = 召回审计；audit_log = L2 审计 | 独立维度 |
| 与 purged_queue 区别 | purged_queue = 物理删排队；audit_log = L2 提案审计 | 不重叠 |

### 3.3 created_at 格式修正（B）
- 草案用 `DEFAULT (datetime('now','localtime'))` → 空格分隔格式，与 memory.py `now()`（T 分隔）不一致
- **定案**：audit_log.created_at 由 **L2 代码用 memory.py `now()` 写入**（与库内 Python 写入路径统一）；不依赖 SQLite DEFAULT
- 或：全表统一改用 SQLite 默认——但存量表已混，**最小改动 = 新表跟 Python 写入一致**

### 3.4 实战影响
- audit_log 0 行（L2 未落地）；唯一风险 = UNIQUE 过严（re-apply 边界，§5.2 测试兜底）

---

## 4. 配套改动（3 schema 同步）

### 4.1 `_migrate_schema()`（存量库）+ `schema.sql`（全新安装）**双改**（A 修正）

```python
# [H-1] _migrate_schema() 加 3 schema 改动 (存量库)
def _migrate_schema(self) -> None:
    for table, col, ddl in [
        ("entities", "user_confirmed", "INTEGER NOT NULL DEFAULT 0"),
        ("chunks", "processed_at", "TEXT"),
        ("entities", "processed_at", "TEXT"),
    ]:
        cols = [c[1] for c in self._conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            logger.info(f"[H-1] migrated {table}: added {col}")

    # user_confirmed partial index [C]
    self._conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_user_confirmed "
                       "ON entities(user_confirmed) WHERE user_confirmed = 1")
    self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_processed_at ON chunks(processed_at)")
    self._conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_processed_at ON entities(processed_at)")

    # audit_log 表 (CREATE TABLE IF NOT EXISTS)
    self._conn.execute(""" ... audit_log ... """)
    # 4 索引
    ...
```

- ⚠️ **`schema.sql` 同步加**（fresh install 走 init_db.py）：3 列 + audit_log 表 + 索引，与 `_migrate_schema` 完全一致
- 两处不一致 = 全新机器装出来与存量迁移结果不同——**验收必须查**

### 4.2 mcp_server.py API 扩展 → **H-1 不做，H0 做**（Q7 verdict）
- `memory_remember` 的 `user_confirmed` kwarg：H0 随 Proposal/Policy 一起加
- `memory_audit_list` / `memory_audit_undo`：H0 实现（TASKS_L2_HYGIENE H0）

### 4.3 validation.py（H-1 可推迟）
- `AUDIT_LOG_STATUSES` / `AUDIT_LOG_PASSES` 校验：H0 落地 audit_log 写入时再加

---

## 5. 验收标准

### 5.1 原有 + cross-check 修正

1. **schema 改动幂等**：重启 MCP N 次不重复 ALTER（f1bc1bf 模式）
2. **存量数据兼容**：4498 entities + 4344 chunks 全获新列；旧数据 user_confirmed=0 / processed_at=NULL
3. **不破坏现有功能**：recall/remember/forget/update/graph_query/stats 6 接口行为不变（4bd654d 同款）
4. **run_purge_worker 不受影响**：4bd654d 代码 + dry_run 验证不动
5. **master 实体不自动 init**：等主人手动
6. **meta.schema_version 不 bump**（f1bc1bf 模式）
7. **H-1/H0 拆 commit**：H-1 = schema-only
8. **【A】schema.sql 与 _migrate_schema 一致**：全新安装（init_db.py）+ 存量迁移后 `PRAGMA table_info` 结果相同
9. **【B】audit_log.created_at 格式统一**：与 memory.py `now()` 一致

### 5.2 新增测试（cross-check 追加）

| 测试 | 场景 | 期望 |
|---|---|---|
| **Q3-1** | 同 run_id：proposed → applied → reverted → **同 run_id re-applied** | **UNIQUE 拦截**（或文档化"re-apply = 新 run_id"） |
| **Q3-2** | 新 run_id：reverted 后 re-applied | 成功（两条 applied 不同 run_id，不冲突） |
| **A-1** | init_db.py 全新装 vs _migrate_schema 存量迁移 → 两 schema 一致 | `PRAGMA table_info` 逐列比对相同 |
| **B-1** | audit_log 写入的 created_at 格式 | 与 `memory.now()` 一致（T 分隔） |

---

## 6. 风险与边界

- **schema-only，不引入业务逻辑**——风险低，仅 ALTER + CREATE
- **run_purge_worker 不动**（4bd654d）——H-1 不影响 purge 行为
- **memory_type 不动**（f1bc1bf）
- **实战现状**：0 个 user_confirmed=1 / 0 个 processed_at NOT NULL / 0 行 audit_log——H-1 后旧数据默认 0/NULL/空表，语义一致
- **表数**：11 → 12 表（audit_log）+ 2 列（user_confirmed, processed_at×2）

---

## 7. 时间窗 + 落地步骤

| 步骤 | 内容 | commit |
|---|---|---|
| H-1 改 | memory.py `_migrate_schema()` + **schema.sql 双改**（A）+ 测试（§5.2） | `feat(H-1): schema 3 项 + 双改一致` |
| H-1 验 | 幂等 + 存量兼容 + 6 接口 smoke + Q3/A/B 测试 | 同 commit |
| H-1 push | push + restart MCP + health check | — |
| H0（下次） | Proposal/Policy/Applier + memory_maintenance + memory_stats 扩展 audit 子键 | `feat(H0): audit_log + L2 基建` |
