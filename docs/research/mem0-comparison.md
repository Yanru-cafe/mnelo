# Mem0 借鉴研究 — 2026-08-11

主人 8/11 拍板把 mnelo README hero 写成 "Knowledge-graph memory layer that
Mem0 charges for" 风格后的衍生动作: 系统读 mem0,产出 mnelo 可借鉴清单.
本档只写当前可借鉴面 + ROI 排序 + 落地建议; 历史已落地项不追溯.

## 1. Mem0 全景 (6 个源交叉验证)

| 源 | 关注点 |
|---|---|
| [`mem0ai/mem0` README](https://github.com/mem0ai/mem0) (8/2026) | 算法路线图 + benchmark 数 |
| [`docs.mem0.ai/platform/overview`](https://docs.mem0.ai/platform/overview) | Platform 商业定位 |
| [`docs.mem0.ai/core-concepts/memory-types`](https://docs.mem0.ai/core-concepts/memory-types) | 4 层 memory + 4 scoping ID |
| [arxiv:2504.19413 (2025-04)](https://arxiv.org/abs/2504.19413) | 原始论文,基础 Mem0 + Mem0g |
| [`mem0.ai/blog/mem0-the-token-efficient-memory-algorithm`](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm) (2026-07) | v3 ADD-only 重构 |
| [`mem0.ai/blog/introducing-temporal-reasoning`](https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0) (2026-07) | 时间推理 (LoCoMo +9.1 pts) |
| [`mem0.ai/blog/introducing-memory-decay`](https://mem0.ai/blog/introducing-memory-decay-in-mem0) (2026-07) | Memory decay 软衰减 |

官方 hero: *"Drop-in memory infrastructure for AI agents and apps. Context
that persists. Built for production."*

## 2. Mem0 5 大架构特性

### 2.1 ADD-only 单遍提取 (v3 重构, 2026-04)
- 一次 LLM call 只 ADD, 不 UPDATE/DELETE; fact 永不被覆盖, history 完整保留
- 2× faster extraction (vs 旧版)
- agent 自身产生的 fact 也是 first-class (跟 user fact 同权重) — 这是
  single-session assistant recall +51.8 pts 的根因 (LongMemEval)
- 设计哲学: "Nothing gets deleted" — 时间改 fact 的 active/inactive,
  而不是删它

### 2.2 Multi-signal retrieval (semantic + BM25 + entity, 并行融合)
- 三路并行评分 → fused 排名
- 只 top match 进 prompt (~7000 tokens vs full-context 25000+)
- 论文基准: **90%+ token 节省** + **91% p95 latency 降低**

### 2.3 Temporal reasoning (2026-07 加层, LoCoMo +9.1 pts @ top_50)
- **write-time enrichment**: 写时做 temporal pass, 提取 event / plan /
  state / relationship / preference / absence 7 类 + time_signature
- **read-time intent classification** (无额外 LLM call): current_state /
  duration_state / upcoming / historical_range / soft_recency 7 mode
- **additive scoring** (软信号, 不改原排序)
- 异步 enrich 不阻塞 write (latency-sensitive workloads)

### 2.4 Memory decay (2026-07, 软衰减 0.3×–1.5×)
- 每 memory 跟踪最近 20 次 access timestamp
- search-time scaling factor, **不删不改**
- fire-and-forget 强化, **median latency +0 ms**

### 2.5 Memory types 4 层 (不是 mnelo 的 6 个 memory_type, 而是 scoping 维度)

| 层级 | 寿命 | 用途 | mem0 实现 |
|---|---|---|---|
| Conversation | 单 turn | tool execution, scratchpad | in-flight only |
| Session | 分钟~小时 | multi-step task | `run_id` scope |
| User | 周~永久 | 个人化 | `user_id` scope |
| Org / Agent | 全局 | 共享 FAQ / 政策 | `agent_id` scope |

**4 个 scoping ID**: `user_id` / `agent_id` / `run_id` / `actor_id`.
`_IDENTITY_KEYS = {"user_id", "agent_id", "run_id", "actor_id"}` —
tenant 隔离铁律, caller 不能通过 metadata 篡改 scope.

## 3. 商业 / DX 包装 (mnelo 真正缺的)

| 包装 | mem0 怎么做 | mnelo 现状 |
|---|---|---|
| **Sign up as agent** | `mem0 init --agent --agent-caller claude-code` — agent 自己签, 无 email 无 dashboard | onboarding 命令缺, owner 必须 ssh 上 VPS |
| **Skills for AI agents** | `npx skills add https://github.com/mem0ai/mem0 --skill mem0` — Claude Code / Codex / Cursor 自动加载 | 没装 skills catalog |
| **Public evaluation framework** | [`memory-benchmarks`](https://github.com/mem0ai/memory-benchmarks) 开源, 任何人复跑 | BENCHMARKS.md 是数字, 不是 harness |
| **Marketing research 页** | LoCoMo / LongMemEval / BEAM 3 个公开 benchmark 全打 | 没独立 research 页 |
| **Migration guide** (v2→v3) | 显式 upgrade path | CHANGELOG 有, 没单独 migration doc |
| **Bootstrap 命令** | `make bootstrap` (server) — 一键启动 + admin + API key | `install.sh` 接近, 但要交互 |

## 4. mnelo 真正值得借鉴的 6 个 ROI 排序清单

按 **实现成本低 + 用户感知强** 排序 (mnelo 是小项目, 优先选能 1-2 PR
落地的).

### ★★★ 高 ROI (强烈推荐)

**借鉴 #1 — ADD-only 提取 / 不删不覆盖 (哲学层)**
- mnelo 现在 `update()` 是 `new chunk + supersede old` (immutable 链),
  本质上已经是 ADD-only, 只是表述
- **行动**: 把 README "knowledge graph native" bullet 改写, 显式提
  "memories accumulate; nothing is overwritten" + 引用 temporal reasoning 思想
- 成本: 文档 5 行

**借鉴 #2 — 4 scoping ID + identity-keys 保护**
- mnelo 现在 `chunks.session_id` 已有, 但只有 1 维
- **行动**: 加 `metadata_json.agent_id` / `user_id` / `run_id` 3 字段
  (~5 处改)
- 成本: 5 处改 + 测试 + **migration 不需要** (JSON K-V 兼容)
- 回报: 多 agent Tailscale mesh 场景立刻受益 (`host:vps-agent-1`
  已有, 但 query 时只能 filter kind 不能 filter agent)

**借鉴 #3 — Time signature + temporal query 7-mode (write-time + read-time)**
- mnelo 现在 `asof` 已有基础时间切片, 但没有 query intent 分类
- **行动**: 在 `_meta_recall` 加 query intent detection
  (current_state / historical / upcoming / soft_recency), 跟 7-mode 对齐
- 成本: 中等, ~200 行 + 测试, 但 mnelo graph 已有 `valid_from` /
  `valid_until`, 架构基础现成

### ★★ 中 ROI

**借鉴 #4 — Memory decay 软衰减 (0.3×–1.5× scaling)**
- mnelo 现在 `recall_count` + `last_recalled` 已有 + importance 字段
- **行动**: 在 `_meta_recall` (或新加 `_decay_recall`) 加 recency-aware
  scaling factor = `1 + 0.5 * exp(-idle_hours / half_life)`, half_life
  按 `memory_type` 分桶 (fact 长, episode 短)
- 成本: 小, ~80 行
- 回报: mnelo 跑半年会发现老 fact 跟新 fact 同权重, 加 decay
  立即改善 recall 排序

**借鉴 #5 — Memory decay via tracked access timestamps**
- mnelo 已有 `last_recalled`, 但 recall_count 不衰减
- **行动**: 在 recall 路径里 fire-and-forget 更新 `last_recalled`
  (已有), 新增 `recall_decay_score` 字段或 computed column
- 跟 #4 一体两面

**借鉴 #6 — Public evaluation harness (memory-benchmarks 模式)**
- mnelo BENCHMARKS.md 是数字, **不是 harness**
- **行动**: 把 `tests/test_recall_*.py` 提到根目录一个 `benchmarks/`
  子包, 加 `python -m benchmarks locomo` 这种入口, 自动跑出 README
  引用的数字
- 成本: 中, 但能直接当 marketing 弹药 ("anyone can reproduce")

### ★ 低 ROI (了解即可, 不急)

- Dream 增量合并 (mem0 8/4 blog 还没写具体方案, 等它先稳)
- 商业 Platform / Self-hosted / Library 三档分发 (mnelo 单一形态足够)
- Skills catalog (主人 fork owner 自维护, 我不能代写)

## 5. 明确**不**借鉴的 (mnelo 比 mem0 强的地方)

| Mem0 设计 | mnelo 已有 / 更好 |
|---|---|
| 强制 LLM 提取 fact | **零 LLM 分类器** (双语正则, ~0ms 开销) |
| Managed cloud 收费 | **本地 SQLite 单文件** (`cp memory.db` = 备份) |
| 三方托管 Qdrant | **usearch f16 / zvec INT8 内嵌** ($10 VPS 跑得动) |
| Vendor lock-in SDK | **标准 MCP** (任何 client 接入) |
| 删除 / 覆盖 fact | **CAS-supersede 不删**, 触发器级联 |

## 6. 落地建议 (优先级路径)

| 阶段 | 内容 | PR 数 |
|---|---|---|
| **P0** | 借鉴 #2: agent_id / user_id / run_id 3 字段 (方案 A), 配 `_meta_recall` filter | 1 |
| **P1** | 借鉴 #4: Memory decay soft scaling, half_life 按 memory_type 分桶 | 1 |
| **P2** | 借鉴 #3: Temporal reasoning 7-mode query intent classification, write-time temporal signature | 1 |
| **P3** | 借鉴 #6: `benchmarks/` 子包 + README 加 benchmark 数 | 1 |

每个 PR 单独跑测试 + 单独 push, 跟主人 8/10 mnelo fork 模式对齐.

## 7. 跟 `docs/COMPARISON.md` 的边界

`docs/COMPARISON.md` (50 行) 是**横向对比表** — mnelo vs Mem0 / Letta /
Zep / Cognee 的 9 维度矩阵 + install footprint. 本档是**深度研究报告**
— 借鉴清单 + ROI 排序 + 落地建议. 两份互补, 不合并 (COMPARISON 矩阵要
短平快, RESEARCH 报告要深全).
