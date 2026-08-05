# 任务分解：Session 状态注入 + 事实晋升机制（TASKS_L2_SESSION_STATE）

> **来源**：8/5 调研"Claude Code 持续正确执行任务的技能/提示词"后的采纳方案（Filesystem-as-State / promote 晋升生命周期）。
> **Part 1（采纳）**：把 mnelo digest 接进 Claude Code **SessionStart 钩子**——搜索公认的"杠杆率最高一处改动"，同时激活已实现但 agent 够不到的 digest。
> **Part 2（借鉴）**：把 `promote` 技能的**晋升生命周期**抄进 L2 整合 pass——高频事实晋升为 canonical_fact、久未召回降级、设上限强制淘汰。
> **现状核对（8/5）**：`Memory.get_digest()` ✅ 已实现（G1-G7）；但 **MCP 工具未注册**、**MneloClient 无 get_digest**、**`inject_on_initialize` config 键是死配置**（config.py:189 有值但无人消费）。Part 1 第一步是补这些缺口。

---

# Part 1 — SessionStart 接 digest（采纳）

## 1.0 目标

Claude Code / Hermes 每次开场自动注入 mnelo digest（身份 + 近期关键决策 + 进行中），Agent 不用主动 recall 就有"当前最重要的事"。对应搜索结果："SessionStart 钩子注入当前状态 = 单点最高杠杆"。

## 1.1 现状缺口（8/5 核对）

| 组件 | 状态 |
|---|---|
| `Memory.get_digest()` 方法 | ✅ 已实现（memory.py:2396） |
| `memory_get_digest` MCP 工具 | ❌ **未注册**（mcp_server.py 无） |
| `MneloClient.get_digest()` | ❌ 未实现（api/mnelo_client.py 无） |
| `[digest] inject_on_initialize` config 键 | ⚠️ 存在（config.py:189）但死配置（无消费） |
| Claude Code SessionStart 钩子 | ❌ 未建 |

## 1.2 任务清单（Part 1）

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **S1** | 注册 `memory_get_digest` MCP 工具 | — | 工具可调 |
| **S2** | `MneloClient.get_digest()` 客户端方法 | S1 | 客户端可调 |
| **S3** | `scripts/session_start_digest.py` 钩子脚本（容错） | S2 | 独立可跑 |
| **S4** | Claude Code `.claude/settings.json` SessionStart 钩子 | S3 | 会话开场注入 |
| **S5** | (可选) G7 MCP initialize 注入（消费死配置键） | S1 | 开关生效 |
| **S6** | 回归 + 容错测试 | S1-S5 | 全绿 + 容错 |

## 1.3 任务详述

### S1 — 注册 `memory_get_digest` MCP 工具（mcp_server.py）

```python
# TOOLS 列表加:
{
    "name": "memory_get_digest",
    "description": "[S1 8/5] 常驻记忆摘要 (DESIGN §4.5 + 可逆压缩 v0.13).
                   无 ref → 摘要压缩视图; ref=<行号> → 展开该行源 chunk.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "行号 (可选, None=返回摘要)"}
        },
    },
}
# _call_tool 分发: "memory_get_digest": ("get_digest", None)
```
**验收**：MCP `memory_get_digest` 无 ref → 摘要；带 ref → 展开源 chunk；非法 ref → 明确错误。

### S2 — `MneloClient.get_digest()`（api/mnelo_client.py）

```python
def get_digest(self, ref=None):
    """[S2 8/5] 常驻摘要双模式. 返回 {content, chunk_id, line_refs, truncated, built_at} 或展开."""
    result = self._call("memory_get_digest", {"ref": ref} if ref else {})
    return result
```
**验收**：客户端调通；与 MCP 工具返回一致。

### S3 — SessionStart 钩子脚本（`scripts/session_start_digest.py`，容错）

```python
#!/usr/bin/env python3
"""[S3 8/5] SessionStart 注入 mnelo digest — 容错: mnelo 不可用则静默.
Claude Code SessionStart 钩子的 stdout 会被注入会话上下文."""
import sys
sys.path.insert(0, "<mnelo_repo>/api")   # 部署时用绝对路径
try:
    from mnelo_client import MneloClient
    d = MneloClient().get_digest()
    content = (d or {}).get("content") or ""
    if content:
        print(f"\n[mnelo-digest]\n{content}\n[/mnelo-digest]")
except Exception:
    pass  # mnelo 未跑/超时 → 不阻断会话启动 (exit 0)
```
**验收**：mnelo 跑 → 输出 digest；mnelo 停 → 无输出、exit 0、不报错。

### S4 — Claude Code SessionStart 钩子（`.claude/settings.json`）

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "", "hooks": [{ "type": "command",
          "command": "python3 /path/to/mnelo/scripts/session_start_digest.py" }] }
    ]
  }
}
```
- **放置**：`~/.claude/settings.json`（用户级，跨项目）或项目 `.claude/settings.json`
- **注意**：Claude Code 钩子以**用户完整权限**执行（安全审计：这是只读调 mnelo，无副作用）
- **验收**：新开会话 → 上下文出现 `[mnelo-digest]` 块；`/clear` 后重启也有

### S5 — (可选) G7 MCP initialize 注入

- 消费 `config.digest_inject_on_initialize`（现在死配置）
- MCP initialize 响应（或首条通知）附带 digest——适合 MCP 客户端而非 shell 钩子场景
- 与 S4 二选一或并存：S4 是 Claude Code 专用；S5 是通用 MCP 客户端
- **验收**：`inject_on_initialize=true` → initialize 含 digest；false → 不含

### S6 — 回归 + 容错测试

- `pytest tests/` 全绿
- 新测试 `tests/test_session_state.py`：
  - digest MCP 工具双模式
  - 钩子脚本 mnelo 跑/停两种状态（停 → 静默 exit 0）
  - 摘要注入不破坏现有 6 核心接口

---

# Part 2 — 事实晋升机制（借鉴 promote 技能，进 L2 整合）

## 2.0 目标

把"高频验证的事实"从 chunk 级晋升为 **canonical_fact 实体**（结构化、稳定、高优先召回）；久未召回的 canonical_fact **降级**；设**上限**强制淘汰。抄 `promote` 技能"已证实的模式晋升为永久规则"思想。

## 2.1 机制设计

| 环节 | 信号 | 动作 |
|---|---|---|
| **晋升** | chunk 满足任一：`recall_count ≥ 20` 或 `evidence_chunk_id 被引用度 ≥ 10` 或 长期（90天）`importance ≥ 0.8` | 抽核心事实 → 建/更新 `canonical_fact` 实体（evidence 指向源 chunk，走 §3.0.4 关系语义） |
| **降级** | canonical_fact 90 天未召回 + 引用度 < 3 | supersede 实体为普通 concept（或软删实体保留 chunk），降级记录 |
| **上限** | canonical_fact 总数 > 50 | 最低 importance 者降级腾位（§3.0 的"entity 是稀缺的"原则） |

- **走审计链**：晋升/降级是 L2 提案（proposal → audit_log → apply），复用 H0 基建，dry-run 默认
- **与 P1a 分类器关系**：晋升只针对 `fact` 类高频 chunk（preference/episode/decision 不晋升——它们是事件/偏好，不是稳定事实）

## 2.2 任务清单（Part 2）

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **P1** | 晋升信号扫描（recall_count / 引用度 / 长期 importance） | H0 audit | 候选识别 |
| **P2** | 晋升动作（chunk → canonical_fact entity，evidence 链接） | P1 | 实体创建 |
| **P3** | 降级 + 上限（90 天未召回 / >50 强制淘汰） | P2 | 降级腾位 |
| **P4** | 审计链接入（proposal → apply）+ 测试 | P1-P3 | dry-run 不破坏 |

## 2.3 任务详述

### P1 — 晋升信号扫描（`_run_promote_pass`）

```python
# 候选: chunk 级 fact, 满足任一强信号
SELECT c.id, c.content, c.importance, c.recall_count,
       COUNT(r.id) as ref_degree
FROM chunks c
LEFT JOIN relations r ON r.evidence_chunk_id = c.id AND r.valid_until IS NULL
WHERE c.valid_until IS NULL AND c.memory_type = 'fact'
GROUP BY c.id
HAVING c.recall_count >= 20 OR ref_degree >= 10 OR (c.importance >= 0.8 AND c.created_at < now-90d)
LIMIT 20
```
- **验收**：候选按信号强度排序；无候选 → 报告 0

### P2 — 晋升动作

- 抽核心事实（规则：内容截断 ≤200 字 + 首句）→ upsert `canonical_fact` 实体
  - `id = "canonical:<slug(content 前 40 字)>"`（§3.10 SEMANTIC 命名空间）
  - 建 `evidence_chunk_id → 源 chunk` 关系（§3.0.4 证据可回溯）
- **验收**：晋升后实体存在 + evidence 链完整；重复晋升 → upsert 不重复

### P3 — 降级 + 上限

- 降级：`canonical_fact` 90 天未召回（`last_recalled < now-90d`）且 `ref_degree < 3` → supersede 为普通 concept（§3.0.3 kind 变更走版本链）
- 上限：count > 50 → 最低 importance 者降级腾位
- **验收**：降级后实体 kind 变更 + 历史保留；上限触发腾位

### P4 — 审计链接入 + 测试

- 晋升/降级是 L2 pass（`run_maintenance(passes=['promote'])`），proposal → audit_log → apply（复用 H0）
- dry-run 默认；confirm 才 apply
- **验收**：dry-run 只报不改；apply 后 audit_log 有 applied 行 + revert_sql；测试覆盖晋升/降级/上限三场景

---

## 3. 执行顺序与依赖

```
Part1: S1 → S2 → S3 → S4 → (S5) → S6
Part2: P1 → P2 → P3 → P4 (依赖 H0 audit 基建, 已实现 448af0b)
```
**分批 commit**：Part1 ① S1-S2（工具+客户端）② S3-S4（钩子+注入）③ S6（回归）；Part2 ① P1-P2（信号+晋升）② P3（降级+上限）③ P4（审计链）

---

## 4. 风险与边界

| 风险 | 缓解 |
|---|---|
| **钩子注入噪音**（每次开场 digest 占上下文） | digest ≤2000 字（护栏）；S4 可选启用；`inject_on_initialize` 默认 false |
| **钩子权限**（Claude Code 钩子全权限执行） | 钩子只读调 mnelo MCP；只打印 digest，无副作用；部署前审计脚本 |
| **晋升误判**（把非事实当 canonical） | 只晋升 `fact` 类 + 强信号（recall≥20 / 度≥10 / 长期高 importance）；dry-run 默认 |
| **canonical_fact 膨胀** | 上限 50 强制淘汰 + 90 天降级 |
| **与 mnelo 现有记忆重复**（promote vs 外部记忆插件） | 本方案是 mnelo 内部机制，不装外部插件（8/5 评估跳过档） |

---

## 5. 参考
- `docs/DESIGN.md` §4.5（digest）/ §4.5.2（可逆压缩）/ §3.0（数据模型：canonical_fact / 实体稀缺）/ §5.2（L2 pass）/ §5.9（审计链）
- `docs/TASKS_L2_DIGEST.md`（G1-G7 已实现，S1-S6 补暴露层）
- 外部借鉴：Filesystem-as-State（SessionStart 注入）/ `promote` skill（晋升生命周期）— 8/5 调研
