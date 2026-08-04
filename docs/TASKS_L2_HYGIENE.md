# 任务分解：L2 P4 记忆卫生 pass（TASKS_L2_HYGIENE）

> **给 hermes 的执行指南**。实现 DESIGN §5.2 P4（记忆卫生 pass）+ 配套 §5.9（原子性/watermark）+ §7.3（健康度 freshness 分量）。
> **背景（hermes 二轮 Q2）**：SearchIndex（P3）已落地，但 P1 卫生 pass 缺失 → 索引/库可能长期漂移。本任务把"应优先补 P1 卫生"落成**有排期、可执行**的任务分解。
>
> **时间窗**：目标 **2026 Q3 末（2026-09-30）前**落地。依赖：SearchIndex A 组（usearch 后端 + A7 repair_index）完成——repair_index 是"事后清"，本任务是"预防 + 衰减"。
> **前置知识**：读者需先读 `docs/DESIGN.md` §5（L2 全章：6-pass 表 / 触发模型 / 矛盾语义 / 规则-LLM 分界 / 安全护栏 / 原子性）。

---

## 0. 目标

让记忆库**主动保持健康**，而不是等它脏了再修：
- **importance 衰减**：久未召回的旧记忆权重随时间下降（§4.9 freshness 同源）
- **TTL 过期**：按 memory_type 给不同生命周期（§3.0.5：ephemeral 短命、procedure 长寿）
- **purge 候选**：衰减到 floor / TTL 过期的进入**非破坏**清理队列（30 天后才物理删）
- **全流程安全**：dry-run 默认、每 proposal 一事务、watermark 幂等、protected 豁免（§5.6/§5.9）

**产出**：L2 `run_maintenance()` 的一个 pass（`hygiene`），与 P1 其它 pass（矛盾检测/消歧）共用 Proposal/Policy/Applier 框架。

---

## 1. 通用契约（复用 L2 基建，勿重新发明）

### 1.1 Proposal / Policy / Applier（DESIGN §5.1/§5.9.1）

```
Proposal = {run_id, pass='hygiene', action, target, before_json, after_json,
            confidence, evidence_chunk_ids, llm_used=False, status}
Policy   = per-pass enable / 阈值 / 批量上限 / protected 豁免 / dry_run
Applier  = 接受提案 → 调 Memory 公开写方法 (forget/update) → audit_log(status='applied')
```

- **每个 proposal 一个事务**（§5.9 决策：细粒度，单条失败不拖垮整批）
- **watermark**：`meta.l2.last_run.hygiene` + `chunks/entities.processed_at`；pass 全成后才推进
- **dry-run 默认**：`run_maintenance()` 不 mutate，只产报告

### 1.2 安全护栏（§5.6 强制）

- `master` 实体 + `user_confirmed=1` 实体 **100% 豁免**（importance 衰减也不碰）
- **floor 保护**：importance 不低于 0.1（DESIGN §5.6 定值）
- **批量上限**：`hygiene` pass 单轮 `decay ≤ 50 / ttl_expire ≤ 50 / purge_candidate ≤ 100`
- **purge 非破坏**：TTL/衰减 → 只入 `purge_candidate` 报告；**物理删除需 `confirm_destructive=true`** 且只走 `purged_queue`（30 天延迟）

---

## 2. 任务清单

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **H1** | `[l2.passes.hygiene]` 配置块 | — | 配置生效 |
| **H2** | importance 衰减算术 | H1 | 单测 |
| **H3** | TTL 规则（按 memory_type） | H1 | 单测 |
| **H4** | purge_candidate 生成（非破坏） | H2-H3 | dry-run 单测 |
| **H5** | watermark + 原子性接入 | H2-H4 | 幂等单测 |
| **H6** | `memory_hygiene_stats` 工具 + `run_maintenance(passes=['hygiene'])` | H5 | 工具可用 |
| **H7** | health_check freshness 分量 + 健康度联动（§7.3） | H6 | 报告含卫生数据 |
| **H8** | 全量回归 + 手动场景验证 | H1-H7 | 全绿 |

---

## 3. 任务详述

### H1 — 配置块（`config.py` + `config.toml.example`）
```toml
[l2.passes.hygiene]
enabled = true          # 默认开（卫生是安全的预防 pass）
dry_run = true          # 默认 dry-run（§5.6）
importance_floor = 0.1
recency_half_life_days = 30      # §4.9 freshness 同源
recall_boost_window_days = 7     # 近期召回的不衰减
purge_after_days = 30            # 与 purged_queue 一致
[l2.caps]
decay = 50
ttl_expire = 50
purge_candidate = 100
```
**验收**：配置可覆盖默认；非法值回落默认 + warning。

### H2 — importance 衰减算术（`memory_maintenance.py` 新模块）

```
# 衰减模型 (DESIGN §4.9 freshness 同源):
#   new_importance = old × (1 - λ·elapsed_freshness)  +  recall_boost
#   λ   = 0.05 (可配, 衰减斜率)
#   elapsed_freshness = 距上次召回/写入的天数, 半衰期 30 天
#   recall_boost = 近 recall_boost_window_days 内被召回 → 不衰减
# 下限: importance_floor = 0.1 (低于不再降, 转 purge_candidate)
```
- **只处理**：`importance > floor` 且非 protected 的 chunk/entity
- **proposal**：`decay_importance`（before/after JSON）
- **验收单测**：30 天未召回 → importance×0.95；7 天内召回 → 不衰减；floor 保护生效；protected 豁免。

### H3 — TTL 规则（按 memory_type，DESIGN §3.0.5 生命周期表）

| memory_type | TTL（未召回/未更新的保留期） |
|---|---|
| `ephemeral` | 7 天 → purge_candidate |
| `fact` | 365 天 |
| `preference` | 180 天（会变，短些） |
| `episode` | 730 天（历史价值） |
| `decision` | 730 天（带理由链） |
| `procedure` | 永久（不 TTL，仅衰减到 floor） |

- **proposal**：`ttl_expire`（设 `valid_until`，走软删链 + 触发器级联）
- **验收**：ephemeral 写入 8 天后跑 hygiene → 生成 ttl_expire 提案；procedure 永不过期。

### H4 — purge_candidate 生成（非破坏）

- 衰减到 floor 且 TTL 过期的项 → 写入报告 `purge_candidates` 列表（**不自动删**）
- 真正物理删除：`run_maintenance(confirm_destructive=true)` → 走 `purged_queue`（30 天延迟，DESIGN §3.8/§5.6）
- **验收**：dry-run 只报告不删；confirm 后入 purged_queue 而非直接 DELETE。

### H5 — watermark + 原子性（§5.9 落地）

- `meta.l2.last_run.hygiene` 推进：pass 内全部 proposal 处理完（applied/skipped）才更新；异常中止不推进
- 每 proposal 一个事务；失败标 `skipped` 继续；返回 `{applied, skipped, failed}`
- **验收单测**：跑两次 → 第二次无副作用（幂等）；中途人为异常 → watermark 不推进、失败项下次重试。

### H6 — 工具接入（`mcp_server.py` + `api/mnelo_client.py`）

- `memory_hygiene_stats()`：报告 importance 分布 / 接近 floor 的项数 / purge 积压（§7.3 freshness 数据源）
- `run_maintenance(passes=['hygiene'], dry_run=true)` 已能选 pass（L2 v0 的 `memory_maintenance` 工具）
- **验收**：工具返回 JSON，含积压数。

### H7 — 健康度联动（§7.3 freshness 分量）

- `health_check` 报告：`hygiene.freshness`（近期写入占比）+ `purge_backlog`（积压数）+ `importance_below_floor`（贴地项数）
- 反馈闭环：purge 积压 > 阈值 → degraded 提示跑维护
- **验收**：health_check 输出含卫生数据；构造积压 → 提示出现。

### H8 — 回归 + 手动场景

- `pytest tests/` 全绿（默认路径零破坏）
- 手动场景：写入 ephemeral 高 importance 旧 chunk → 跑 hygiene（dry-run）→ 报告含 ttl_expire 提案 → confirm 后入 purged_queue

---

## 4. 执行顺序

```
H1 → H2 → H3 → H4 → H5 → H6 → H7 → H8
（H2/H3 可并行，H5 依赖二者产物）
```
**分批 commit**：① H1-H2（配置+衰减）② H3-H4（TTL+purge）③ H5（watermark）④ H6-H7（工具+观测）⑤ H8（回归）

---

## 5. 时间窗与依赖

| 里程碑 | 目标 |
|---|---|
| H1-H4（衰减/TTL/purge 核心） | Q3 中（2026-08 末） |
| H5-H7（watermark/工具/观测） | Q3 末（2026-09 前） |
| H8（回归+手动验证） | 与 H7 同步 |

**依赖**：`audit_log` 表（P1 L2 v0 先行项）——hygiene 的 proposal/applied 落审计；若 audit_log 未落地，先用临时的 `hygiene_report` 表过渡，P1 完整后迁移。

---

## 6. 风险

| 风险 | 缓解 |
|---|---|
| 衰减误伤近期重要但低召回的项 | recall_boost_window + floor 保护 + dry-run 默认 + 报告列"将跌出检索阈值的实体"（§5.6） |
| TTL 误删（episode/decision 有历史价值） | 长 TTL（730 天）+ 只入 purge_candidate 不直接删 + confirm 才物理删 |
| 与 SearchIndex 索引不同步（hygiene 软删了 chunk 但索引还在） | A7 repair_index 清理；hygiene 的 ttl_expire 提案 apply 时**同步调 index.remove**（接线点：Applier 调 `Memory.forget` 时 `_index` 已联动） |
| 无 audit_log 过渡 | 先用 `hygiene_report` 表，P1 完整后迁入 audit_log |

---

## 7. 参考
- `docs/DESIGN.md` §3.0.5（类型生命周期表）/ §4.9（freshness）/ §5（L2 全章）/ §5.6（护栏）/ §5.9（原子性）/ §7.3（健康度）
- `docs/TASKS_SEARCH_INDEX.md` A7（repair_index + drift——与卫生 pass 互补）
- `scripts/repair_vectors.py`（purged_queue / 清理模式参考）
