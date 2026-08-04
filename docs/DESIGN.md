# mnelo 顶层设计蓝图

> **定位**：本文件是 mnelo 的**演进蓝图**——描述目标架构、各层设计与演进路线。
> **现状基线**：`ARCHITECTURE.md`（当前实现分析）、`SCHEMA.md`（SQL schema 参考）。
> **版本**：v0.6 · 2026-08 · 依据 7/21 修复后的代码状态（vec0 查询、asof、init_db、路径已修）。
> **v0.3 变更**：全方位专家评审后补入——产品边界（§1.4）、记忆类型谱系（§3.0）、双轨组织模型（§4.8）、新近度加权（§4.9）、来源可信度（§4.10）、并发与保留（§3.9）、工具收敛（§6.5）。
> **v0.4 变更**：采纳 hermes agent 评审反馈——P1 提取拆 P1a(规则)/P1b(LLM)（§5.2）、correct() 与 user_confirmed 边界明确化（§3.7）、工具收敛提前到 P1 末（§9）、git 快照改 `VACUUM INTO` 且不进主仓（§3.8）。
> **v0.5 变更**：Q4 修正——快照改 `sqlite3 .backup` → `snapshots/YYYYMMDD.db.gz` 归档、rsync 到 NAS，git 跟踪二进制方案排除；修正 DB 体积基线（实测 44.72MB+WAL，README ~24MB 已过期）。Q5——健康度权重不预设，P2 等权 + 0.6 警戒线起步。
> **v0.6 变更**：§3.0 从"记忆类型谱系"扩展为**正式数据模型**——记忆=chunk+entity+relation 双表示、三对象边界定义、kind×memory_type 双谱系正交澄清、entity 建置判定规则、relation 语义（weight/confidence/evidence 分工）。
> **约定**：`P0/P1/P2/P3` = 演进阶段，见 §9。所有设计遵循现有六条 design tenets（local-first / 单文件 / 标准 MCP / 双语 / boring & predictable / measured）。
> **借鉴来源**：标 `⟵ 借鉴 <系统>` 的条目，其思路来自对 Mem0 / Letta(MemGPT) / Zep(Graphiti) / Cognee / LangMem / SuperMemory / Hindsight 的调研（2026-08），按 mnelo 的 local-first 单机约束裁剪。

---

## 1. 现状评估

### 1.1 已核对的架构事实

| 维度 | 现状 |
|---|---|
| 核心类 | 单巨石 `Memory`（`memory.py` ~1470 行）：CRUD + 4 路召回 + RRF + 实体管理 + 统计 + 清理 |
| 存储 | 单文件 SQLite，11 表（entities/chunks/relations/vectors/recall_log/purged_queue/meta + 4 触发器） |
| 召回 | 4 路：vector（sqlite-vec vec0）/ graph（2-hop BFS）/ meta（`LIKE %q%`）/ entity（name/alias），RRF（k=60）融合 |
| 时态 | 双时态软删除（valid_from/valid_until + created_at/updated_at），superseded_by 链 + 触发器级联 |
| 协议 | MCP over SSE（127.0.0.1:8086），10 个工具，Bearer token + loopback-only + 限流 60/min/tool |
| 可观测性 | 17 个 Prometheus 指标（运维 RED 视角），`recall_log` 审计表 |
| 客户端 | `api/mnelo_client.py` MneloClient（每调用新建 SSE 连接） |
| 校验 | `validation.py` 已完善（8KB/1KB 上限、控制字符/bidi/零宽清洗、ValidationError 带字段） |

### 1.2 结构性短板（本蓝图的动因）

1. **无记忆生命周期**——纯被动存储。存什么、何时作废全由调用方（Agent）决定；无提取、无矛盾检测、无整合、无记忆卫生。
2. **单巨石无分层**——存储/检索/实体管理耦合，无存储抽象缝，"可迁移路径"无从谈起。
3. **双时态不完整**——chunks 无 `valid_from`；向量在 update/forget 时**物理删除**，语义层无法回放历史。
4. **检索层三个缺陷**——meta 路 `LIKE %q%` 无索引 O(N)；entity 路不匹配实体 **id**（只 name/alias）；RRF 的 `method` 标签被后处理的 lane 覆盖，污染召回反馈环数据。
5. **协议层有旁路**——`memory_entity_resolve` / `memory_list_entities` / `memory_search_relations` 三个工具是 **raw-SQL handler**（`_CUSTOM_HANDLERS`），绕过 `Memory` 类直查 SQLite，无分页、无统一校验。
6. **无召回质量评测**——`benchmark.py` 只测延迟；`recall_log` 里的 `recall_details_json`（每 hit 的 method/rank/score）**无人消费**做质量分析。17 个指标全是运维视角，没有 precision@k。
7. **写路径无显式事务/回滚**——remember/update 的 chunk+entities+relations+vector 多步写入依赖 Python sqlite3 的隐式事务（无 `BEGIN`/`ROLLBACK` 包裹）。中途异常（如 embed 失败）时隐式事务保持打开，单例连接复用下后续操作可能把部分数据一并提交。

### 1.3 现存正确设计（保留，不推倒重来）

- **4 路召回 + RRF**：rank-only 融合，跨异质检索路线的教科书级正确选择
- **双时态 + 软删除 + 触发器级联**：DB 强保证的图一致性（`superseded_by` 时引用边自动失效）
- **证据可回溯**：每条 relation 带 `evidence_chunk_id`，可 1 条 SQL 追回原文
- **identity_fact 不可变 + 白名单**：防身份伪造的设计，值得泛化
- **validation.py**：输入清洗面完整（含 Trojan Source bidi 防护）
- **占位符查询过滤**：保护 recall_log 信号纯度

### 1.4 产品边界（mnelo 是什么 / 不是什么）
防止 scope creep 的定位声明，所有设计决策以此为准绳：

| mnelo 是 | mnelo 不是 |
|---|---|
| 本地优先的**记忆存储 + 检索层** | 完整 Agent 运行时（Agent 永远是调用方，不是被 mnelo 托管） |
| 显式可回溯的**个人知识图谱** | 通用知识库/文档系统（不做富文本、协作、权限） |
| 提供**自主维护管线**（可选） | 替主人做判断的记忆体（决策权留在 Agent/人） |
| 单机、单文件、可备份 | 分布式 / 多租户平台 |

**推论**：新增能力必须回答"它是在让记忆被更好地**存取**，还是在让 mnelo 变成 Agent？"——后者应拒绝或外包。

---

## 2. 目标架构：5 层

```
┌─────────────────────────────────────────────────────────┐
│  L3 协议层  MCP tools (统一契约) / 客户端                    │
├─────────────────────────────────────────────────────────┤
│  L2 记忆管理层 (新增)  6-pass 自主维护管线 + audit 可撤销    │
├─────────────────────────────────────────────────────────┤
│  L1 检索层  4-lane + RRF + 质量评测                        │
├─────────────────────────────────────────────────────────┤
│  L0 存储层  SQLite(图/时态/实体/证据真相源) + search-index  │
│            适配器(向量+FTS 可插拔)                          │
├─────────────────────────────────────────────────────────┤
│  L4 可观测性  Prometheus 指标 + recall_log 质量反馈闭环     │
└─────────────────────────────────────────────────────────┘
```

| 层 | 现状 | 优化方向 |
|---|---|---|
| **L0 存储层** | 11 表，双时态不完整 | **记忆类型谱系（§3.0）**、chunk.valid_from、FK、向量软删保留历史、**search-index 适配器**、schema 迁移、写路径事务化 + **纠正传播/写入去重/git 快照/并发与保留**（§3.7-3.9） |
| **L1 检索层** | 4 路 + RRF | FTS5、entity id 匹配、RRF 标签修正、**双轨组织（显式容器树）/ 常驻摘要 / 多跳推理 / 会话隔离 / 新近度加权 / 来源可信度**（§4.5-4.10）、质量评测 harness |
| **L2 记忆管理层 (新增)** | 无 | **6-pass** 自主维护管线（含社区检测），Proposal/Policy/Applier，dry-run 默认，审计可撤销 |
| **L3 协议层** | 10 工具，3 个 raw-SQL 旁路 | **工具收敛到 ~10（§6.5）**、消除旁路、批量/分页、客户端长连接 |
| **L4 可观测性** | 17 运维指标 | 召回质量指标 + **记忆健康度评分**、反馈闭环 |

**核心原则**：分层但**不引入进程/服务依赖**——仍是单进程、单文件、local-first。分层的意义在**职责边界与可替换缝**，不在部署形态。

---

## 3. L0 存储层

### 3.0 正式数据模型（chunk / entity / relation）

#### 3.0.1 核心问题：一条「记忆」是什么

**记忆（Memory）** = 一个原子的事实陈述 / 偏好 / 事件 / 决策 / 流程，可被独立召回、作废、版本化。它是 mnelo 的领域概念。

存储上采用**双表示（dual representation）**：
- **原文表示（chunk）**：人类可读的完整陈述——保真、可回溯
- **结构化表示（entity + relation）**：图谱化的概念节点与语义边——可导航、可推理

> **一条记忆 = chunk（原文）+ 零或多个 entity（它提及的概念）+ 零或多个 relation（概念间的边）**。
> 两条设计结论由此而来：① chunk 永远保留原文（可回溯的根基）；② entity/relation 只是"从 chunk 里抽出来的索引视图"，**从不携带 chunk 没有的信息**（信息单源）。

#### 3.0.2 三对象边界（正式定义）

| 对象 | 定义 | 例 | ID | 时间语义 |
|---|---|---|---|---|
| **chunk** | 一条**原文陈述**（非结构化、保真） | "7/15 建仓 sh600089 12000@18.96" | 生成 id（`chunk_ts_seq`） | `timestamp`（陈述时间）+ `valid_until`（作废时间） |
| **entity** | 一个**可指称的概念**（结构化、可复用） | sh600089 / 特变电工 / 主人 | 语义 id（`sh600089`、`identity:predicate:value`），全局唯一 | `valid_from`/`valid_until`（概念有效窗口）+ `superseded_by`（版本链） |
| **relation** | 一条**有向语义边** | `建仓_于` / `located_in` | 自增 + 组合唯一约束 | `valid_from`/`valid_until` + `evidence_chunk_id` |

**判定规则（何时建 entity）**——概念满足任一条件即应建 entity：
- (a) 会被**跨 chunk 引用**（去重/合并有价值）
- (b) 有**别名**（一物多名，需归一）
- (c) 有**属性**需要稳定承载（如持仓数量、时区）
- (d) 是**图导航锚点**（主人、股票、项目、常驻摘要）

否则只写 chunk（纯陈述，无引用价值）——**防止 entity 爆炸**（个人库规模下 entity 是稀缺的，chunk 是廉价的）。

#### 3.0.3 双谱系正交：kind × memory_type

entity 上有**两个正交维度**，不是层级关系：

| 维度 | 回答 | 决定什么 | 例 |
|---|---|---|---|
| **kind**（概念角色） | 这个节点在图里**扮演什么** | **结构行为**（identity_fact 不可变、user 是主人锚点、stock 走符号别名强制、container 是收纳节点） | stock / concept / identity_fact / container |
| **memory_type**（记忆类型） | 这条记忆**生命周期如何** | **生命周期行为**（fact 可作废要校验、preference 可被纠正覆盖、episode 永不合并、procedure 优先保留、ephemeral 短 TTL） | fact / preference / episode / decision / procedure / ephemeral |

**正交性澄清（关键）**：
- 一个 entity 同时有 `kind` 和 `memory_type`，二者独立。例：`sh600089` = kind `stock` × memory_type `fact`
- **`memory_type` 的权威载体是 chunk**（记忆的类型）。entity 上的 `memory_type` 是**便捷冗余/派生**：当 remember 不指定 entity 类型时继承 chunk 的类型；当同一 entity 被多条不同类型的记忆共享时，反映最近/主要关联，**不保证严格**
- **`identity_fact` 的不可变规则来自 kind，不来自 memory_type**——一个 `kind=identity_fact, memory_type=fact` 的实体不可变；一个 `kind=concept, memory_type=fact` 的实体可正常作废

#### 3.0.4 关系语义（正式）

```
relation = (source_id, target_id, relation_label, weight, confidence,
            evidence_chunk_id, valid_from, valid_until)
```

- **`relation_label`**：开放字符串，但遵循命名规范（`<谓词>`：`建仓_于`、`located_in`、`is_identity_fact_for`），同义谓词必须归一（L2 消歧职责）
- **`evidence_chunk_id`（可回溯保证）**：每条边必须"生于"一条原文 chunk；边不携带证据链之外的信息
- **weight vs confidence 分工**：`weight` = 边强度（语义上多强）；`confidence` = 来源可信度（这条边多可靠）。§4.10 来源可信度进排序用的是 confidence/source
- **relation 没有 memory_type**——边的类型由它的证据 chunk 决定，不重复标注

#### 3.0.5 记忆类型谱系（生命周期行为，同 §3.0.3 memory_type 维度的展开）

| 类型 | 语义 | 生命周期 | 关键规则 |
|---|---|---|---|
| `fact` 事实 | 持股、住址、能力 | 单调、可作废 | 校验严格；作废要证据 |
| `preference` 偏好 | 报告风格、沟通习惯 | 会变 | 纠正传播（§3.7）；可被新偏好覆盖 |
| `episode` 事件 | 某日建仓、某次对话 | 不重复、带时间点 | 永不合并；时态回溯主对象 |
| `decision` 决策 | 为什么买/不买 | 带理由链 | 需回溯理由（evidence 链）；不轻易作废 |
| `procedure` 步骤/流程 | 周报怎么写 | 稳定、可复用 | 优先保留；重复写入去重 |
| `ephemeral` 瞬时 | 临时草稿 | 短命 | TTL 短；低 importance |

**收益**：L2 提取器知道"要提什么类型"；矛盾检测按类型定规则（fact 可作废、procedure 几乎不作废）；卫生按类型定 TTL/衰减；召回可按类型过滤。

#### 3.0.6 开放决策点
- **entity.memory_type 语义**：本设计定为"便捷冗余 + 继承默认"（见 3.0.3）——如实施中发现误导（召回按类型过滤 entity 路时误判），可改为 entity 路完全忽略 memory_type、只按 kind 过滤
- **chunk 是否可无 entity 关联**：允许（纯陈述），entity 是可选索引视图
- **多语句 chunk**：一条 chunk 应承载**一个原子记忆**；复合陈述（"既建仓又清仓"）应由调用方拆分，或由 L2 P5 整合拆分

### 3.1 双时态补全
- `chunks` 增加 `valid_from`（现状只有 `timestamp` + `valid_until`，无法表达"从 T1 到 T2 有效"）
- 全表统一语义：`有效于 asof T ⟺ valid_from ≤ T AND (valid_until IS NULL OR valid_until > T)`
- 向量历史：**改为软删除**（update/forget 不物理删向量，靠 valid_until 过滤），让语义层也能回放历史；代价是 vec0 行数增长，需配 `repair_vectors` 类清理任务

### 3.2 约束强化
- FK：`relations.source_id/target_id → entities.id`、`relations.evidence_chunk_id → chunks.id`（`PRAGMA foreign_keys=ON` 已开）
- 注意：FK 需要实体先存在；remember 的写序（先实体后关系）已满足，需补测试覆盖

### 3.3 全文检索
- meta 路从 `LIKE %q%`（无索引 O(N)）换成 **FTS5**（`chunks_fts` 虚拟表 + 触发器维护，见 §4.1）
- 保留 LIKE 作为回退/精确匹配通道（FTS5 的 tokenizer 对股票代码 `sh600089` 可能切碎）

### 3.4 写路径事务化
- `remember()` / `update()` 的 chunk+entities+relations+vector 多步写入包**显式事务**（`BEGIN`/`COMMIT`，异常 `ROLLBACK`），杜绝部分写入

### 3.5 Schema 迁移机制
- 基于 `meta.schema_version`（现 1.0）建立正式迁移流程：`scripts/migrate/*.py` 逐版本升级，禁止跳版本
- 现有 `migrate_to_mnelo.py` 归入此框架

### 3.6 存储适配器（可迁移路径的缝）
```
Memory
 ├─ StorageBackend (graph + temporal + entities)   → SQLite 默认; 未来 Neo4j 可选
 └─ SearchIndex (vector + FTS)                     → sqlite-vec + FTS5 默认; 未来 zvec / Qdrant/Milvus
```
- `Memory` 内部定义两个薄接口：`GraphStore`（节点/边/时态查询）与 `SearchIndex`（embed + KNN + FTS 匹配）
- L2 的所有变更也走这两个接口，**永不 raw SQL**——未来换后端只动适配器
- 默认实现保持 SQLite 单文件（不引外部服务）

### 3.7 写路径增强：实体纠正传播 + 写入去重 ⟵ 借鉴 Mem0
现状 `update()` 只换 chunk，**不改实体和关系**——"特变电工改名了"不会联动实体 name/aliases 和引用它的边。这是比 L2 更基础的一层，两个能力：

- **实体纠正传播（self-editing）**：新增 `Memory.correct(entity_id, changes)` 动作——更新实体属性/别名 + 级联更新指向它的关系属性 + 记录 `superseded_by`
  - **不可变边界（明确化）**：`master` 用户实体 = **100% 不可变**（任何路径含 correct() 都拒绝）；其它 `user_confirmed=1` 实体**仅豁免 L2 自动 pass**，`correct()` 显式调用仍允许；`identity_fact` 走专用路径（identity_fact_manager）
  - 这样既防"自主层悄悄改主人身份"，又不堵死"主人自己明确要改"的唯一入口
- **写入时去重（NOOP 决策）**：`remember()` 可选开关 `dedup_check=True`——写入前检索同主语同谓词的现存事实，命中则走 update/合并而非新增。默认关（保持显式语义 + 写入低延迟），L2 仍负责事后清理

### 3.8 记忆快照（版本化备份）⟵ 借鉴 Letta MemFS
Letta 2026 年把记忆改成 git 版本化的文件系统。mnelo 移植为轻量版，但**针对 SQLite 单文件 + WAL 的实际情况修正**：

- **备份方式**：周期（cron / post-write 低频）用 **`sqlite3 .backup`** 生成一致性快照。⚠️ **不要直接 `cp memory.db`**——WAL 模式下写入中的文件可能拷到中间页；备份 API 会正确包含 WAL 中未 checkpoint 的数据
- **产物归档**：`snapshots/YYYYMMDD.db.gz`（`.backup` 后 gzip），**不进 git**、不进源码主仓——单独 **rsync 到 NAS / bigbox**（与现有 `backups/pre-update-*.zip` 模式一致）。`git` 跟踪二进制完全没必要
- **体积实测（修正 README 基线）**：主人真实库 **44.72 MB 主体 + 0.72 MB WAL**，比 README 声称的 ~24MB 大近一倍（README 该基线已过期，待更新）。按 ~45MB 算：日快照 + gzip ≈ 5-10MB/份，保留 30 份 ≈ 150-300MB，可接受；**若日快照 + git 跟踪二进制，一年后 .git 膨胀 ~16GB——已排除该方案**
- 与现有 `valid_until` 版本链互补：库内版本链管"单条事实的历史"，快照管"**整个库的时间旅行**"（diff / 回滚 / 灾难恢复）
- 复用仓库已有的 `.githooks/post-commit` 基建触发备份脚本（产物进 `snapshots/`，不进主仓）

### 3.9 并发模型与日志保留
- **并发模型（明说）**：单进程内**单写者**（唯一 `Memory` 实例持有写连接）+ WAL + 多读者（recall 的 4 路并发读是读连接）。多客户端（Hermes/Claude/Cursor 同时连）共享同一写者；冲突策略 = busy_timeout + 写事务串行。**不引入多写者**——违反即触发 §1.4 边界审查
- **日志保留策略**：`recall_log` 与新增 `audit_log` 无限增长。策略：recall_log 保留 N 天 / M 条（聚合后入 stats）；audit_log 保留更久（可撤销的价值），但提供归档/清理工具；均走 `purged_queue` 通道统一管理

---

## 4. L1 检索层

### 4.1 meta 路 → FTS5
- `chunks_fts`（content + source + session_id），BM25 排序与 `importance` 加权结合
- 查询改写：`LIKE %q%` → `MATCH`，对含符号的 token（`sh600089`、`D∩W`）保留 LIKE 回退
- 收益：O(N) 全扫 → 索引检索；时间/重要度过滤下推到 SQL

### 4.2 entity 路补 id 通道
- 现状只匹配 `name LIKE` / `aliases_json LIKE`；查询 `sh600089`（实体 id）时 entity 路空手而归
- 补 `id LIKE` 通道（优先级：id > name > alias），并修正"按 id 查实体"的召回空窗

### 4.3 RRF method 标签修正
- 现状：`_rrf_fuse` 对同 `chunk_id` 的后处理 lane 覆盖 hit dict，导致显示 `method='meta'` 掩盖实际贡献 lane，**污染 recall_log 反馈环**
- 修正：同一 chunk 多 lane 命中时，保留**最高 rank 分数的 lane** 作为 method（或存 lane 集合 `methods: [...]`）

### 4.4 召回质量评测 harness
- 消费 `recall_log.recall_details_json`（已存每 hit 的 method/rank/distance/rrf_score/importance）
- 对标 LongMemEval 思路建立轻量评测：**时态正确率**（asof 回放是否符合预期）+ **lane 贡献分布**（每 lane 命中率、空窗监控）
- `scripts/benchmark.py` 升级：从纯延迟 → 延迟 + 召回质量双指标

### 4.5 常驻记忆摘要 ⟵ 借鉴 Letta core memory
- 现状：最该记住的事（身份/关键决策）也要靠检索碰运气
- 借鉴：`Memory` 自动维护一份 **500–2000 字的记忆摘要**（主人身份 + 近期高 importance 决策），提取规则进 L2；新 MCP 工具 `memory_get_digest` 或 MCP initialize 时自动注入 Agent 上下文
- 摘要本身也是 chunk（`source='digest'`），可更新/作废，遵循同一套双时态
- 与 identity_facts 的关系：identity_facts 是"结构化身份事实"，摘要是"面向 Agent 上下文的压缩叙事"，二者互为视图

### 4.6 多跳路径推理 ⟵ 借鉴 Cognee CoT graph traversal
- 现状：图路只有 2-hop BFS 返回邻居，回答不了"X 和 Y 怎么连起来的"
- 借鉴：新增 `Memory.reason(start_id, end_id, max_hops=4)`，返回**完整路径链**
  ```
  entity1 --relation--> entity2 --relation--> entity3 --relation--> entity4
  ```
  带每跳的 evidence_chunk_id（保持证据可回溯）。SQL 层用递归 CTE（SQLite 3.8+ 支持），比 N+1 循环更稳
- 新 MCP 工具 `memory_reason`

### 4.7 会话级召回隔离 ⟵ 借鉴 Mem0 scoping
- 现状：chunks 有 session_id，但 recall 不按会话过滤——对话 A 会串到对话 B 的上下文
- 借鉴：`recall(session_id=...)` 时把 meta/vector 路的过滤条件加上 `session_id`；不传则全局（默认，保持兼容）
- 与 L1 现有 filters 参数整合（filters 增加 `session_id` 键）

### 4.8 双轨组织模型：显式容器树 + 语义涌现
组织是记忆系统的根问题。现状只有**涌现轨道**（向量相似 + 社区聚类），缺**显式收纳轨道**——"我亲手把这条放进某个区域"。二者互补：语义检索解决"忘了放哪"，显式结构解决"我知道它该在哪"。参考记忆宫殿（method of loci）的组织原则，但实现为通用容器树：

- **显式轨道**：实体 `kind='container'`（或 `locus`）+ `contains` 关系边形成树。几乎零 schema 改动——容器就是实体，收纳就是边
  - `remember(location=<容器id>)` 建收纳边
  - `recall(location=...)` 把召回限定在容器子树内（graph/meta 路自然支持，vector 路加过滤）
  - 容器带 `order` 属性支持路线巡游（宫殿式的按序访问）
- **涌现轨道**：向量相似 + L2 P6 社区检测（自动聚类，人不用管）
- **约束（防止两条轨道打架）**：`location` 只是召回的一个**可选约束**，不是新主路径；默认不传=全局语义召回。显式结构是"加固"，不是"替代"
- 新工具 `memory_loci_*`（建容器 / 放置 / 子树导航），并入 §6.5 工具收敛后的"组织"意图组

### 4.9 新近度加权进 RRF ⟵ 借鉴 Zep/Graphiti 时态检索
现状：asof/valid_until 让"某时点有效"**可查**，但**排序时不奖励新近度**——昨天的重要事实和半年前的同等重要事实同分。个人记忆里"现在的相关度"几乎总该加权：

- RRF 融合后加一个**时态新鲜度因子**：`final = rrf_score × (1 + λ · freshness(valid_window, recall_time))`
- `freshness` 随时间平滑衰减（如半衰期 30 天）；`λ` 可配（默认小，避免压制相关性）
- 与 asof 正交：asof 决定"哪些有效"，freshness 决定"有效里哪些更当下"

### 4.10 来源可信度进入排序
现状：relations 有 `confidence`、chunks 有 `source`，证据链有 `evidence_chunk_id`，但**来源可靠性没进排序**：

- 定义来源可信度档：`user_confirmed`（主人确认，最高）> `manual` > `agent` > `import:*`（脚本导入）> `digest`（自动摘要）
- `source` 前辍映射到权重，在 RRF 融合时给 chunk/entity 加分
- 与 L2 卫生联动：低可信来源的低 importance 项，优先进入 TTL/清理候选

---

## 5. L2 记忆管理层（新增 · 轻量自主层）

> 这是本蓝图的核心新增。原则一句话：**可选的、异步的、永不悄悄破坏数据的记忆维护管线**。

### 5.1 设计原则
1. **显式写入保持默认路径**——`remember/update/forget` 的行为在 L2 开/关时**完全一致**；开启时写路径只多一个**非阻塞原子 dirty 标志**
2. **所有变更先成为提案**——L2 永不直接改数据；每个动作写成 `Proposal` → 过 `Policy` 门槛 → 写 `audit_log`（status=`proposed`）→ 显式启用才 apply
3. **dry-run 是默认**——`run_maintenance()` 默认不改变任何数据，只产出报告
4. **幂等**——每 pass 有 watermark（`meta.l2.last_run.<pass>` + `chunks.processed_at`），重复运行无副作用

### 5.2 六个 pass

| Pass | 输入 | 输出提案 | 复用 | LLM 可选 |
|---|---|---|---|---|
| **P1a 提取·规则模板** | 新 chunks（`processed_at IS NULL`） | 高置信实体/关系（stock 符号+中文名、身份陈述模板等） | 复用 entity_resolve 的 stock-probe 模式（符号+中文名强制）、模板 | ✗ 纯规则，零依赖 |
| **P1b 提取·LLM** | P1a 未覆盖的 chunks | 自由文本事实/实体 | 向量相似度找已有实体、aliases 归一 | ✅ 自由文本；**无 LLM 时跳过** |
| **P2 矛盾检测** | 提案事实 + 当前有效事实 | `supersede_relation` / `update_entity_property` | valid_until 链 + 级联触发器 | ✅ 语义矛盾；规则只做精确谓词 |
| **P3 实体消歧** | 候选对 | `merge_entities` | `entity_resolve.find_duplicate_candidates` + embedding 相似度 | ✅ 中置信度裁决 |
| **P4 记忆卫生** | importance + recall_log | `decay_importance` / `ttl_expire` / `purge_candidate` | recall_count、purged_queue | ✗ 纯规则 |
| **P5 整合** | 高相似 chunks | `merge_near_duplicate_chunks` / `summarize_old` | chunks.superseded_by 链 | ✅ 摘要仅 LLM |
| **P6 社区检测** ⟵ Zep communities | 关系图 + 实体 | `create_community` / `refresh_community_summary` | 图上聚类（标签传播/Louvain 近似）+ 社区摘要存 entity | ✅ 社区摘要仅 LLM |

共享核心：**Proposal / Policy / Applier**
```python
Proposal = {run_id, pass, action, target, before_json, after_json,
            confidence, evidence_chunk_ids, llm_used, status}
Policy   = per-pass enable/阈值/批量上限/protected 豁免/dry_run
Applier  = 接受提案 → 调 Memory 公开写方法 → audit_log(status='applied')
```

### 5.3 触发模型
| 触发 | 形态 | 角色 |
|---|---|---|
| 防抖 post-write 钩子 | 写路径只设 dirty 原子标志，异步 worker 静默期（如 60s）后跑 | **主**（长驻进程时新鲜度最好） |
| 按需 MCP 工具 `memory_maintenance` | 手动/定时调用 | **常驻原语**（不依赖进程存活） |
| 系统 cron | 调工具 | 可选（headless 场景） |

任何触发最终都收敛到同一个 `run_maintenance()`；三种形态是配置选择，不是代码分叉。

### 5.4 矛盾语义：supersede，永不 delete
自动作废仅在**四条件同时成立**时触发：
1. 新事实 `confidence ≥ 0.75`
2. 置信度优势 `F.confidence − E.confidence ≥ 0.20`（margin 是防过度作废的关键护栏）
3. 证据更新：`F.evidence_chunk.timestamp > E.evidence_chunk.timestamp`
4. 非 protected（`user_confirmed=0`）

触发后：`E.valid_until=now`、`E.superseded_by=F_id`，触发器级联失效引用边——**旧数据仍可随时态查询**，非破坏。

不满足 → **不改任何数据**，把双方写入 `audit_log` 的 `conflict_candidate`，供 `memory_merge_confirm` / 人工裁断。

### 5.5 规则 vs LLM 分界（行为矩阵）
| Pass | 无 LLM（离线默认） | 有 LLM（可选，Ollama 保离线） |
|---|---|---|
| 提取 | **P1a 规则模板**（stock 符号+中文名、身份陈述；高精度低召回，近空但非零） | P1a + **P1b 自由文本** |
| 矛盾 | 精确谓词 + 值不同才提案 | 语义矛盾（"age 32" vs "33" 跨谓词） |
| 消歧 | 高阈值自动 + 中档转人工 | 中置信度自动裁决 |
| 卫生 | 完整 | 完整 |
| 整合 | 近重复合并 | + 摘要 |

**关键立场**：无 LLM 时提取 pass 应当**近空而非"模板猜"**——垃圾事实进入图谱是自主层最糟的失败模式。宁可高精度低召回，不可低精度高污染。

### 5.6 安全护栏
- **dry-run 默认**；apply 需显式全局/per-pass 开关
- **append-only `audit_log`**：含 before/after JSON、pass、confidence、evidence、`llm_used`、`revert_sql`
- **撤销**：`memory_audit_undo(audit_id)` 重放 revert_sql（所有 L2 动作都是软写，撤销天然安全）
- **阈值护栏**：矛盾 margin≥0.20、自动合并相似度≥0.95、importance 下限 0.1
- **批量上限**：如 supersede≤20 / merge≤20 / purge≤50，单次病态运行不可级联
- **protected 标记**：`entities.user_confirmed`（master 用户实体及显式确认项豁免一切自动变更）
- **隔离而非销毁**：衰减到 0 / TTL 过期 → 只入 `purge_candidate` 报告；破坏性 purge 需 `confirm_destructive=true` 且只动 purged_queue
- **回退退避**：某 pass 的已 apply 动作被撤销后，下次运行该 pass 提高最低置信度

### 5.7 Schema / API 影响
- 新表 `audit_log`：`id, run_id, pass_name, action_type, ref_type, ref_id, before_json, after_json, confidence, llm_used, status('proposed'|'applied'|'reverted'|'skipped'), created_at, revert_sql`
- 新列：`chunks.processed_at`（可空、索引）、`entities.user_confirmed`（int、索引）
- 复用 `meta` 表存 L2 配置（`l2.enabled`、`l2.dry_run`、`l2.running`、`l2.last_run.<pass>`）
- **新 MCP 工具**：
  - `memory_maintenance(passes[], dry_run=true, since)` — 跑 L2
  - `memory_audit_list(run_id?, status?, pass?)` — 审阅决策
  - `memory_audit_undo(audit_id)` — 撤销已 apply 动作
  - `memory_merge_confirm(proposal_id)` — 把 proposed 提升为 applied（桥接现有手动 entity_resolve 流）
  - `memory_hygiene_stats()` — importance/TTL/purge 积压报告
  - `memory_get_digest()` — 常驻记忆摘要（§4.5，⟵ Letta）
  - `memory_reason(start_id, end_id, max_hops)` — 多跳路径推理（§4.6，⟵ Cognee）
  - `memory_topics()` — 社区/话题概览（§5.2 P6，⟵ Zep）
  - `memory_correct(entity_id, changes)` — 实体纠正传播（§3.7，⟵ Mem0）
- 配置块：
```toml
[l2]
enabled = false
dry_run = true
[l2.passes]
extract    = { enabled = false, llm = false, min_conf = 0.7 }
contradict = { enabled = true,  margin = 0.20, min_conf = 0.75 }
dedup      = { enabled = true,  auto_threshold = 0.95 }
hygiene    = { enabled = true,  importance_floor = 0.1 }
consolidate= { enabled = false, llm = false }
[l2.caps]
supersede = 20
merge = 20
purge = 50
[l2.hook]
enabled = false
debounce_s = 60
[l2.llm]
enabled = false
backend = "ollama"
```

### 5.8 风险与缓解（Top 5）
| 风险 | 缓解 |
|---|---|
| 1. 提取噪声污染图谱（RRF 被拉偏） | LLM 门控；无 LLM 近空；提案先行；置信度下限；全软写可撤销 |
| 2. 自动作废过度、误删好数据 | 四条件 + 0.20 margin；双时态不删；dry-run 默认；protected 豁免；批量上限 |
| 3. importance 衰减/整合悄悄改变排序 | 纯算术可逆 + floor；dry-run 报告列"将跌出检索阈值的实体"；before/after 全记录 |
| 4. 双语误合并（中英文名真实不同实体） | 自动合并阈值≥0.95；中档转人工/LLM；软合并可恢复；protected 不合并 |
| 5. 离线/在线双模式割裂成两个不一致产品 | 显式 per-(pass×llm) 行为矩阵；单 `run_maintenance()` 入口，模式只是配置差 |

---

## 6. L3 协议层

### 6.1 消除 raw-SQL 旁路
- `memory_entity_resolve` / `memory_list_entities` / `memory_search_relations` 三个 `_CUSTOM_HANDLERS` 下沉为 `Memory` 方法（`list_entities()` / `search_relations()`），统一走校验 + 单一访问点
- 收益：旁路工具获得与核心工具同等的校验、指标、时态过滤语义

### 6.2 批量操作
- `memory_remember_many`（批量写入，事务包裹）、`memory_forget_batch`
- 服务端单一事务，客户端少开连接

### 6.3 分页
- `memory_list_entities` / `memory_search_relations` 加 `cursor`（基于 id 的游标分页，而非 offset）

### 6.4 客户端
- `MneloClient` 复用 SSE 长连接（现每调用新建连接）；`timeout` 参数真正生效（现未传入底层）

### 6.5 工具收敛（反蔓延）
现状 10 个 + L2/借鉴 新增后 ~19 个，**这是真实错误方向**——LLM Agent 工具选择本身就是错误源，工具越多越容易选错。收敛原则：**按意图分组为高层工具，内部派发**，目标 ~10 个：

| 意图组 | 工具（收敛后） | 合并现状 |
|---|---|---|
| 写 | `memory_write`（remember/update/correct/batch） | remember / update / relate / correct / remember_many |
| 查 | `memory_recall`（含 location/session/type/asof/filters） | recall（吸收多跳推理与摘要为策略参数） |
| 组织 | `memory_loci`（建容器/放置/子树导航） | 新增（§4.8） |
| 维护 | `memory_maintenance`（6-pass + dry-run） | 新增（L2） |
| 审阅 | `memory_audit`（list/undo/merge_confirm） | 新增（L2） |
| 图谱 | `memory_graph`（query/reason/topics） | graph_query / reason / topics |
| 管理 | `memory_stats` / `memory_hygiene_stats` | 现 stats + 新增 |

- 保留 `forget` 为独立工具（危险动作显式化，不让它藏在 write 里）
- 客户端 `MneloClient` 同步收敛为高层方法

---

## 7. L4 可观测性

### 7.1 补召回质量指标
| 指标 | 含义 | 数据源 |
|---|---|---|
| `mnelo_recall_precision_at_k` | 召回质量（对已知标注集） | 评测 harness |
| `mnelo_recall_lane_hits_total` | 每 lane 命中占比（已有 `method` label 的 recall_total 升级） | recall_log |
| `mnelo_recall_empty_rate` | 空结果率（已部分有 `recall_hits_total`） | recall |

### 7.2 反馈闭环
- `health_check.py` 日报告消费质量数据：空窗 lane、asof 时态正确率、importance 分布
- 异常告警：如某 lane 命中率连续下滑 → 提示检查 embedder 模型 / 数据质量问题

### 7.3 记忆健康度评分 ⟵ 借鉴 Hindsight + 可观测性
把 17 个散落指标收敛成**一个可行动的复合评分**（0–100），`health_check` 呈现：
| 分量 | 指标 | 警戒线 |
|---|---|---|
| 覆盖率 | 活跃实体/关系 vs 总历史 | 活跃占比骤降 |
| 新鲜度 | 近期写入占比、过期未清比例 | purged_queue 积压 |
| 去重度 | 重复实体候选数（`find_duplicate_candidates`） | >50 组 |
| 平衡度 | 各 lane 命中率方差 | 单 lane <5% 且持续 |
| 健康度 | 以上加权合成 | <0.6 提示需要维护 |

**健康度 v0 公式（P2 实施时定，不在 DESIGN 阶段假装拍权重）**：5 个分量量纲不同（覆盖率是 0-100% 占比、新鲜度是衰减时间、去重度是离散计数、平衡度是方差），设计阶段预设权重是伪精确。决策：P2 起步 **全分量等权归一化 + 0.6 警戒线**，上线后按真实数据调参（哪个分量先触线就调哪个）。

一句话回答"我的记忆是不是变脏了"，L2 的 run 报告直接喂给这个评分。

---

## 8. 可迁移路径（存储选型）

### 8.1 为什么不是 Postgres / 独立向量库
- **Postgres/pgvector**：个人单机工具引入服务器进程 + 连接管理 + 备份复杂度，负收益；个人规模（≤50 万向量）用不上其能力，且会破坏"cp memory.db = 备份"的 core tenet
- **独立向量库（Qdrant/Milvus）**：违背 local-first，且只解决 4 路召回中的 1 路——mnelo 的硬骨头（图/时态/证据）它们一点忙帮不上

### 8.2 zvec：首选升级候选
> 事实更正：zvec 是**阿里巴巴**开源（`github.com/alibaba/zvec`，Apache-2.0），非腾讯（腾讯云社区发过介绍文造成混淆）。

- **为什么值得**：进程内嵌（`pip install` 即用，符合 local-first）、真 HNSW/DiskANN、**原生 FTS + BM25**——恰好补上 mnelo 两个真实短板（sqlite-vec 暴力扫描 + meta 路无索引 LIKE）
- **边界**：解决不了图路/时态（那些留在 SQLite）；双存储需设计同步（update/forget 向量作废、asof 过滤、chunk↔向量映射）；项目年轻（v0.6.0，2026-07）

### 8.3 适配器分档
| 档位 | 向量 | FTS | 触发条件 |
|---|---|---|---|
| **今日** | sqlite-vec（零依赖） | SQLite FTS5 | 默认 |
| **升级** | **zvec**（HNSW + 原生 FTS） | zvec 原生 | 向量 >~50 万 或 meta 路延迟超标 |
| **超大规模** | Qdrant/Milvus | 独立 | >千万向量 / 分布式 |

迁移由 §3.6 的 `SearchIndex` 适配器封装，业务代码零改动。

---

## 9. 演进路线图

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **P0** | L0：**记忆类型谱系（§3.0）**、chunk.valid_from、FK、FTS5、写事务、**并发与保留模型（§3.9）**、schema 迁移框架、**实体纠正传播 + 写入去重（§3.7）**、**git 快照（§3.8）**；L1：entity id 匹配、RRF 标签修正、**新近度加权（§4.9）**、**会话级召回隔离（§4.7）**、质量评测 harness | — |
| **P1** | L2 v0：audit_log + 矛盾检测 + 消歧 pass（规则优先），dry-run 跑通，`memory_maintenance` 工具；L1：**多跳路径推理 `memory_reason`（§4.6）**、**常驻记忆摘要 `memory_get_digest`（§4.5，规则版先上）**、**双轨组织 `memory_loci`（§4.8）**、**来源可信度加权（§4.10）**；**P1 末尾执行工具收敛（§6.5）**——新工具随 L2 落地即收敛，不让 agent 长期面对 ~19 个工具 | P0 |
| **P2** | L2 完整：提取（P1a 规则 + P1b LLM）+ 卫生 + 整合 + **社区检测 `memory_topics`（§5.2 P6）**；L4 质量闭环（precision@k + **健康度评分 §7.3** + health_check 反馈） | P1 |
| **P3** | L3：消除旁路、批量/分页、客户端长连接；存储适配器落地（zvec 试用） | P0-P2 |

每阶段独立可交付、可回滚，不阻塞其他阶段。

---

## 10. 决策记录（ADR 摘要）

| 决策 | 结论 | 理由 |
|---|---|---|
| 存储形态 | 保持 SQLite 单文件真相源 | local-first、零运维、图/时态/证据是 SQLite 强项 |
| 自主层自主度 | 轻量可选，非全自动 | 保持 boring & predictable；防垃圾事实污染 |
| LLM 依赖 | 零依赖默认，LLM 纯可选 | 保持离线能力；无 LLM 时提取近空 |
| 向量历史 | 软删除保留（P0），换取语义回放 | 与"measured"原则一致，接受 vec0 增长 |
| meta 路 | FTS5（SQLite）优先于 zvec | 零新依赖即可补短板；zvec 留作升级档 |
| 触发模型 | 防抖钩子 + 按需工具双轨 | 长驻/短命进程都覆盖，幂等收敛到单一入口 |
| 常驻摘要 | 摘要即 chunk（双时态管理） | 复用现有软删/版本机制，不为摘要引入第二套状态 |
| 实体纠正 | 走 `Memory.correct()` 级联，受 identity_fact 不可变约束 | 比 L2 更基础的写路径能力，先于自主层落地 |
| 写入去重 | `dedup_check` 默认关 | 保持显式语义与写入低延迟；L2 负责事后兜底 |
| 快照 | git 快照 + valid_until 版本链互补 | 库内版本管单条历史，快照管整体时间旅行 |
| 社区摘要 | 摘要仅 LLM（可选），聚类纯规则 | 与 L2 其它 pass 的规则/LLM 分界一致 |
| 产品边界 | 存储+检索层，非 Agent 运行时 | 防 scope creep；新增能力过"边界审查" |
| 记忆类型 | 六类谱系（fact/preference/episode/decision/procedure/ephemeral） | 提取/矛盾/卫生按类型定规则 |
| 组织模型 | 双轨（显式容器树 + 语义涌现），`location` 仅可选约束 | 语义搜索与显式收纳互补，不互斥 |
| 新近度 | RRF 加 freshness 因子（半衰期可配） | 个人记忆"当下的相关度"应加权 |
| 可信度 | 来源档位映射权重，进 RRF | 已有 confidence/source 字段用满 |
| 工具面 | 收敛到 ~10 个高层工具，意图分组 | LLM 工具选择是错误源，越少越好 |
| 并发 | 单写者 + WAL + 多读者，不引多写者 | 边界声明 + 事务串行 |

---

## 11. 与现有文档的关系

- **`ARCHITECTURE.md`**：现状实现分析，保持不变（本蓝图的事实基线）
- **`SCHEMA.md`**：当前 SQL schema，P0 落地时随迁移更新
- **`DESIGN.md`（本文件）**：演进蓝图，含目标架构与路线图；阶段落地后同步回写 ARCHITECTURE/SCHEMA
