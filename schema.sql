-- ========================================
-- mnelo schema v1.0
-- 文件: ~/.hermes/memory/schema.sql
-- : 4D 知识图谱 (节点 + 关系 + 时间 + 向量)
-- 主人口中 7/18 拍板 review SCHEMA.md
-- ========================================

-- 1. ENTITIES (节点) ----------------------------------
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                     -- stock / concept / event / person / chunk / task / canonical_fact
    memory_type TEXT DEFAULT 'fact',        -- [P0 §3.0] fact / preference / episode / decision / procedure / ephemeral
    name TEXT,
    summary TEXT,
    properties_json TEXT,
    aliases_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    valid_from TEXT,
    valid_until TEXT,
    superseded_by TEXT,
    source TEXT,
    importance REAL DEFAULT 0.5,
    recall_count INTEGER DEFAULT 0,
    last_recalled TEXT,
    user_confirmed INTEGER NOT NULL DEFAULT 0,  -- [H-1 §1] 0=未确认, 1=主人显式确认; §3.7 L2 保护
    processed_at TEXT                              -- [H-1 §2] NULL=未跑过 L2; TASKS H5 watermark 候选
);
CREATE INDEX idx_entities_kind ON entities(kind);
CREATE INDEX idx_entities_updated ON entities(updated_at);
CREATE INDEX idx_entities_valid ON entities(valid_from, valid_until);
CREATE INDEX idx_entities_supersede ON entities(superseded_by) WHERE superseded_by IS NOT NULL;
-- [H-1 C 修正] partial index — user_confirmed=1 实战 0-N 个, 全表索引低选择性
CREATE INDEX idx_entities_user_confirmed ON entities(user_confirmed) WHERE user_confirmed = 1;
CREATE INDEX idx_entities_processed_at ON entities(processed_at);

-- 2. CHUNKS (原文块) ----------------------------------
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT DEFAULT 'fact',        -- [P0 §3.0] fact / preference / episode / decision / procedure / ephemeral
    source TEXT,
    session_id TEXT DEFAULT 'default',
    timestamp TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    importance REAL DEFAULT 0.5,            -- 0.0-1.0, 排序 (entities 表也有, 冗余存方便排序)
    metadata_json TEXT,
    superseded_by TEXT,
    valid_until TEXT,
    recall_count INTEGER DEFAULT 0,
    last_recalled TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    processed_at TEXT                              -- [H-1 §2] NULL=未跑过 L2; TASKS H5 watermark 候选
);
CREATE INDEX idx_chunks_timestamp ON chunks(timestamp);
CREATE INDEX idx_chunks_source ON chunks(source);
CREATE INDEX idx_chunks_session ON chunks(session_id);
CREATE INDEX idx_chunks_importance ON chunks(importance);
CREATE INDEX idx_chunks_valid ON chunks(valid_until) WHERE valid_until IS NOT NULL;
CREATE INDEX idx_chunks_processed_at ON chunks(processed_at);

-- 3. RELATIONS (边) ----------------------------------
CREATE TABLE relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    properties_json TEXT,
    valid_from TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    source TEXT,
    confidence REAL DEFAULT 1.0,
    evidence_chunk_id TEXT
);
CREATE INDEX idx_relations_src ON relations(source_id);
CREATE INDEX idx_relations_tgt ON relations(target_id);
CREATE INDEX idx_relations_relation ON relations(relation);
CREATE INDEX idx_relations_valid ON relations(valid_from, valid_until);
CREATE INDEX idx_relations_evidence ON relations(evidence_chunk_id);

-- 4. VECTORS (向量索引, sqlite-vec 0.1.x) ----------
-- vec0 是单列虚拟表: embedding + rowid, 与 chunks.id 1:1 映射 (rowid -> chunk_id)
-- dim 在 init_db.py 运行时从 config.toml 读 (默认 512) — 切 embedding 模型必须重新 init_db
CREATE VIRTUAL TABLE vectors USING vec0(
    embedding float[{EMBED_DIM}]
);

-- 5. META (系统元数据) -------------------------------
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 6. RECALL_LOG (召回审计) ----------------------------
CREATE TABLE recall_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    query_embedding_id TEXT,
    results_json TEXT,
    graph_hops INTEGER,
    latency_ms REAL,
    recall_details_json TEXT,   -- [P2+ #3 7/18 patch]  feedback loop: top-5 ranks + method + distance/rrf_score + importance
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_recall_query ON recall_log(query);
CREATE INDEX idx_recall_created ON recall_log(created_at);

-- 7. PURGED_QUEUE (软删除→物理删除) ------------------
CREATE TABLE purged_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    purged_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    done INTEGER DEFAULT 0
);
CREATE INDEX idx_purged_done ON purged_queue(done);
CREATE INDEX idx_purged_target ON purged_queue(target_id);

-- 7.5 AUDIT_LOG (L2 自主层审计) --------------------
-- [H-1 §3] TASKS_L2_HYGIENE H0 的核心表 — Proposal/Policy/Applier 落审计
-- 实战: 0 行 (L2 未落地); UNIQUE 防同 run_id 重复 apply 同 ref
-- created_at 由 L2 代码用 memory.now() 写 (T 分隔), 不依赖 SQLite DEFAULT
-- 见 TASKS_H1_SCHEMA.md §3.2 / §3.3 (deepseek B 修正)
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,              -- UUID 标识一次 L2 pass run
    pass_name TEXT NOT NULL,            -- 'contradict' / 'dedup' / 'hygiene' / 'consolidate' / 'extract' / 'topics'
    action_type TEXT NOT NULL,          -- 'decay_importance' / 'ttl_expire' / 'merge_entities' / ...
    ref_type TEXT NOT NULL,             -- 'chunk' / 'entity' / 'relation'
    ref_id TEXT NOT NULL,
    before_json TEXT,                    -- apply 前快照
    after_json TEXT,                     -- apply 后快照
    confidence REAL DEFAULT 1.0,
    llm_used INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'proposed',  -- 'proposed'/'applied'/'skipped'/'reverted'
    created_at TEXT NOT NULL,            -- [B 修正] 不依赖 SQLite DEFAULT, 由 L2 代码用 memory.now() 写
    revert_sql TEXT,                     -- 仅 applied 状态有; undo 时重放
    UNIQUE(run_id, pass_name, action_type, ref_id, status)  -- 防同 run 重复 apply
);
CREATE INDEX idx_audit_log_run ON audit_log(run_id);
CREATE INDEX idx_audit_log_pass ON audit_log(pass_name, status);
CREATE INDEX idx_audit_log_ref ON audit_log(ref_type, ref_id);
CREATE INDEX idx_audit_log_created ON audit_log(created_at);

-- ========================================
-- 7.6 TASK_STATES + STATE_TRANSITIONS (任务/循环状态机 M1 schema)
-- ========================================
-- [8/6 owner-approved] 主人 DESIGN_TASK_LOOP.md §3 DDL 拍板落地.
-- 设计文档: docs/DESIGN_TASK_LOOP.md (474 行). M1 仅落 schema + 不变量 + seed;
-- API (M3) / 行为 (M2) / digest (M4) / cron tick (M5) 后续落地.
--
-- 不变量 1: 同一 task 同时最多 1 个当前状态行 (ux_task_current_state partial UNIQUE)
-- 不变量 2: state 必须在 task (6) / loop (3) 词汇集 — CHECK 约束
-- 不变量 3: kind IN ('task','loop') 必须被 L2 TTL/decay pass 排除 (D11, M5 落地)
--
-- task_states 不参与向量索引 — 结构化状态, 不是 recall 候选 (DESIGN §3.4).
CREATE TABLE task_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,              -- entities.id (kind='task' 或 kind='loop')
    state TEXT NOT NULL CHECK (state IN (
        'open','in_progress','waiting','blocked','done','cancelled',  -- task 6 词
        'running','dormant','paused'                                  -- loop 3 词 (loop 状态仅生命周期事件落行; tick 不落, §4.3)
    )),
    valid_from TEXT NOT NULL,           -- 进入该状态时刻, memory.now() (T 分隔)
    valid_until TEXT,                   -- 离开时刻, NULL = 当前状态 (CAS 关旧窗 = SET valid_until=now)
    reason TEXT,                        -- 语义摘要: 为什么进入该状态 (含 actor 痕迹, §7)
    evidence_chunk_id TEXT,             -- 接地: 支撑这次转移的 chunk (推荐必填, 创建可空)
    created_at TEXT NOT NULL,           -- [B 修正风格] 不依赖 SQLite DEFAULT
    FOREIGN KEY (task_id) REFERENCES entities(id),
    FOREIGN KEY (evidence_chunk_id) REFERENCES chunks(id)
);

-- 不变量 1 — partial UNIQUE index (实战测试 test_ux_task_current_state_rejects_double_open)
CREATE UNIQUE INDEX ux_task_current_state
    ON task_states(task_id) WHERE valid_until IS NULL;

-- 当前活跃任务快查 (排除 done/cancelled/dormant/paused)
CREATE INDEX idx_task_states_open
    ON task_states(state) WHERE valid_until IS NULL
      AND state NOT IN ('done','cancelled','dormant','paused');

-- asof 回放 (按 task 取全部窗)
CREATE INDEX idx_task_states_task_valid
    ON task_states(task_id, valid_from, valid_until);

-- 允许的转移图 (§3.2). scope='default' = 全局默认; 具体 loop_id 可覆盖 (M2)
CREATE TABLE state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'default',   -- 'default' 全局 / 具体 loop_id 覆盖
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    UNIQUE(scope, from_state, to_state)
);

-- 默认转移矩阵 (DESIGN §3.2, M1 seed). loop 状态 (running/dormant/paused) 不在此表 —
-- loop 状态由 loop_tick 机械判定 (§4.3), 仅生命周期事件 (create/disable/enable/pause) 落行.
INSERT OR IGNORE INTO state_transitions (scope, from_state, to_state) VALUES
    -- task 状态 (6 词): open / in_progress / waiting / blocked / done / cancelled
    ('default', 'open',        'in_progress'),
    ('default', 'open',        'done'),
    ('default', 'open',        'cancelled'),
    ('default', 'in_progress', 'waiting'),
    ('default', 'in_progress', 'blocked'),
    ('default', 'in_progress', 'done'),
    ('default', 'in_progress', 'cancelled'),
    ('default', 'waiting',     'in_progress'),
    ('default', 'waiting',     'done'),
    ('default', 'waiting',     'cancelled'),
    ('default', 'blocked',     'in_progress'),
    ('default', 'blocked',     'waiting'),
    ('default', 'blocked',     'done'),
    ('default', 'blocked',     'cancelled'),
    -- done -> open 是 reopen 逃生门 (D8, 需 reason)
    ('default', 'done',        'open');
    -- cancelled 不在转移图里 (terminal, §3.2)

-- ========================================
-- 触发器 (自动维护)
-- ========================================

-- 8.1 维护 updated_at
CREATE TRIGGER trg_entities_updated AFTER UPDATE ON entities
BEGIN UPDATE entities SET updated_at = datetime('now', 'localtime') WHERE id = NEW.id; END;

CREATE TRIGGER trg_chunks_updated AFTER UPDATE OF superseded_by, valid_until ON chunks
BEGIN UPDATE chunks SET created_at = created_at WHERE id = NEW.id; END;
-- (chunks 表不用 updated_at, 触发器保持 created_at 不变)

-- 8.2 entity 被 supersede 时, 自动级联失效引用边 (核心创新!)
CREATE TRIGGER trg_entities_supersede AFTER UPDATE OF superseded_by ON entities
WHEN NEW.superseded_by IS NOT NULL AND OLD.superseded_by IS NULL
BEGIN
    UPDATE relations
    SET valid_until = datetime('now', 'localtime')
    WHERE (source_id = OLD.id OR target_id = OLD.id) AND valid_until IS NULL;
END;

-- 8.3 chunk 被 supersede 时, 自动级联失效引用边
CREATE TRIGGER trg_chunks_supersede AFTER UPDATE OF superseded_by ON chunks
WHEN NEW.superseded_by IS NOT NULL AND OLD.superseded_by IS NULL
BEGIN
    UPDATE relations
    SET valid_until = datetime('now', 'localtime')
    WHERE (source_id = OLD.id OR target_id = OLD.id) AND valid_until IS NULL;
END;

-- ========================================
-- 初始化 meta (schema_version + embedding 模型)
-- model + dim 占位符在 init_db.py 运行时替换 (env > config.toml > default)
-- ========================================
INSERT INTO meta (key, value) VALUES
    ('schema_version', '1.1'),
    ('embedding_model', '{EMBED_MODEL}'),
    ('embedding_dim', '{EMBED_DIM}'),
    ('created_at', datetime('now', 'localtime')),
    ('created_by', 'mnelo v0.5.x'),
    -- [H-1 8/4 fix] 审计 §2: L2 启用 flag, H0 落地时 query 这个值
    -- =1 表示 audit_log 表已建 (H-1 落地); 0/missing 表示 H-1 未跑
    ('l2_audit_log_ready', '1'),
    ('l2_h1_migrated', datetime('now', 'localtime'));

-- ========================================
-- 启用 WAL mode + busy_timeout (避免 lock 复发!)
-- ========================================
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
PRAGMA foreign_keys = ON;
