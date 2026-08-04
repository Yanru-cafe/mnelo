# 任务分解：常驻记忆摘要 + 可逆压缩（TASKS_L2_DIGEST）

> **给 hermes 的执行指南**。实现 DESIGN §4.5（常驻记忆摘要，⟵ Letta core memory）+ §4.5.2（可逆压缩，⟵ Headroom CCR，v0.13）。
> **解决的问题**：最该记住的事（身份/近期决策）不该靠召回碰运气——摘要压缩进 Agent 上下文，需要细节时按指针展开。
> **前置**：✅ **P1a 分类器已上线**（b1e32c4/e8e41d2/aacc983）——digest 的"近期关键"块按 classified memory_type 过滤；⚠️ H0（audit_log）**不需要**（digest 是确定性派生视图，非 L2 提案链）。
> **配套文档**：DESIGN §4.5/§4.5.1/§4.5.2、§5.7 工具清单。
> **时间窗**：可独立交付。

---

## 0. 背景与目标

**现状**：Agent 每次会话上下文是全新的（不跨 session），只能靠 `recall()` 碰运气找回重要事实。P1a 分类器已上线后，chunk 有真实的 memory_type——digest 现在有了可靠的输入。

**目标**：
1. 自动维护一份 **500–2000 字常驻摘要**（身份 + 近期关键决策/事件 + 进行中会话），Agent 开场即有"当前最重要的事"
2. **可逆压缩**：摘要行带 provenance 指针，`memory_get_digest(ref=...)` 按需展开到原始 chunk
3. digest 自身是 chunk（`source='digest'`），双时态 supersede，历史可回放

**原则**：
- **信息单源**（§3.0.1）：摘要行信息 chunk 全有，绝不引入 chunk 没有的内容
- **保真优先**：摘要宁可截断也不失真；截断行可经指针取回原文（v0.13）
- **boring & predictable**：规则构建（无 LLM 默认），dirty 触发增量重建，确定性

---

## 1. 契约

### 1.1 digest chunk 格式

```python
# digest 是一个普通 chunk，用约定字段标识：
chunk = {
    "id": generate_id("chunk"),
    "content": <摘要文本, 500-2000字, 分行为"行">,
    "source": "digest",
    "memory_type": "fact",          # 派生元数据, 不让 P1a 再分类 (classify 跳过 source='digest')
    "metadata_json": {
        "digest": True,
        "line_refs": {"1": ["chunk_a"], "2": ["chunk_b", "chunk_c"], ...},  # 行索引 → 源 chunk ids
        "truncated": False,          # 2000 字超限截断标志
        "built_at": "<ISO>",
    },
    "superseded_by": <旧 digest id 或 None>,
    "valid_until": None,
}
```

### 1.2 meta 状态

```
meta: digest_dirty    = 0/1    # 有新 identity_fact / 高 importance decision → 1
      digest_chunk_id = <当前 digest chunk id>   # 未建 = NULL
```

### 1.3 工具签名

```python
# MCP tool: memory_get_digest(ref=None)
#   ref=None  → {"content": <摘要文本>, "chunk_id": <digest chunk>, "truncated": bool, "built_at": str}
#   ref=<行索引> → {"source_chunks": [<原始 chunk 列表>]}    # 可逆压缩展开 (§4.5.2)
# 客户端: MneloClient.get_digest(ref=None)
```

### 1.4 配置（`config.py` + `config.toml.example`）

```toml
[digest]
enabled = true              # 默认开（纯派生视图，无副作用）
max_chars = 2000            # §4.5.1 体积护栏
recent_window_days = 30     # 块2 时间窗
importance_threshold = 0.8  # 块2 高 importance 门槛
inject_on_initialize = false # 可选：MCP initialize 自动注入（默认显式调用）
```

---

## 2. 任务清单

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **G1** | digest 状态：meta 键 + `[digest]` 配置块 | — | 配置生效 |
| **G2** | `_build_digest()` 三块构建（含 line_refs） | G1, P1a | 三块正确 + 指针 |
| **G3** | dirty 追踪：remember() 置位 + get_digest 触发重建 | G2 | dirty 语义 |
| **G4** | digest chunk 生命周期：重建 supersede + 双时态 | G3 | 历史可回放 |
| **G5** | `memory_get_digest` 双模式工具 + 客户端 | G2-G4 | 双模式可用 |
| **G6** | 可逆压缩指针生命周期（v0.13） | G4 | 指针稳定 |
| **G7** | MCP initialize 注入（可选） | G5 | 开关生效 |
| **G8** | 回归 + 测试矩阵 | G1-G7 | 全绿 |

---

## 3. 任务详述

### G1 — digest 状态 + 配置

- `config.py`：`[digest]` 块（§1.4），env `MNELO_MEMORY_DIGEST_*` 覆盖
- `meta` 表：`digest_dirty` / `digest_chunk_id`（`_migrate_schema` 不用加表——meta 是 KV，直接 upsert 键）
- **验收**：config 可读；meta 键可写。

### G2 — `_build_digest()` 三块构建（核心）

```python
def _build_digest(self) -> Tuple[str, Dict[str, List[str]], bool]:
    """返回 (text, line_refs, truncated). 纯规则, 无 LLM."""
    lines, refs = [], {}
    n = 0
    # 块1 身份: identity_facts (kind='identity_fact', valid_until IS NULL)
    for f in <SELECT id, predicate, value FROM identity_facts active ORDER BY importance DESC>:
        n += 1; lines.append(f"身份: {predicate} = {value}")
        refs[str(n)] = [f["id"]]          # 指针指向 identity_fact 实体
    # 块2 近期关键: importance>=0.8 AND memory_type IN ('decision','episode')
    #   AND timestamp >= now-30d AND valid_until IS NULL, ORDER BY importance DESC LIMIT 20
    for c in <...>:
        n += 1; lines.append(<首句规则截断, 50字上限>)
        refs[str(n)] = [c["id"]]
    # 块3 进行中: 最近 5 个 session 的主题 (session_id 分组, 取每 session 最新 chunk)
    for s in <最近 5 个活跃 session>:
        n += 1; lines.append(f"会话 {s['session_id']}: <主题=最新 chunk 首句>")
        refs[str(n)] = [s["latest_chunk_id"]]
    # 体积护栏: 拼接 <= max_chars, 超限截断 + truncated=True
    return "\n".join(lines), refs, truncated
```

- **line_refs 对齐**：每行一个 `refs[str(行号)]`——`memory_get_digest(ref="3")` 即取第 3 行的源 chunk
- **验收单测**：三块都存在；身份块 = 实际 identity_facts；近期关键块只含 decision/episode；line_refs 与行号一一对应

### G3 — dirty 追踪

```python
# remember() 末尾 (已分类后):
#   若 chunk.memory_type in ('decision','episode') AND importance >= 0.8
#   或写入的 entities 含 identity_fact:
#       meta 设 digest_dirty = 1
# memory_get_digest():
#   if meta.digest_dirty == 1: self._rebuild_digest()   # G4
#   返回当前 digest chunk
```
- **验收**：写高 importance decision → dirty=1；get_digest → 重建 + dirty 清零；无 dirty → 返回缓存（不重建）

### G4 — digest chunk 生命周期（双时态）

```python
def _rebuild_digest(self) -> str:
    text, refs, truncated = self._build_digest()
    old_id = meta.digest_chunk_id
    new_id = generate_id("chunk")
    # INSERT 新 digest chunk (source='digest', memory_type='fact', metadata_json={digest, line_refs, truncated, built_at})
    if old_id:
        # UPDATE 旧 digest: superseded_by=new_id, valid_until=now  (双时态, 历史可回放)
    meta.digest_chunk_id = new_id; meta.digest_dirty = 0
    return new_id
```
- **验收**：连续两次 dirty 重建 → 旧 digest valid_until 置位 + superseded_by 指向新；`asof` 能回放旧 digest

### G5 — `memory_get_digest` 双模式工具

- `mcp_server.py` 加工具（schema 见 §1.3）+ `_handle_get_digest`
- `api/mnelo_client.py` 加 `MneloClient.get_digest(ref=None)`
- **ref=None** → 返回摘要；**ref=行号** → `line_refs[str(ref)]` 取源 chunk ids → 返回完整 chunk 列表
- **验收**：`get_digest()` 返回摘要；`get_digest(ref="2")` 返回第 2 行源 chunk；非法 ref → 明确报错

### G6 — 可逆压缩指针生命周期（v0.13）

- **源 chunk supersede** → 指针指向版本链：`line_refs` 存最新版 id，digest 重建时刷新（§4.5.2 "指针随重建刷新"）
- **digest 自身 supersede** → 旧 digest 的 `line_refs` 留在其 metadata_json（历史可回放）
- **truncated 截断行** → 仍可 `get_digest(ref=...)` 取回原文（截断 ≠ 丢信息）
- **验收**：源 chunk update 后重建 digest → 指针指向新版本；旧 digest 展开仍工作

### G7 — MCP initialize 注入（可选）

- `[digest] inject_on_initialize`（默认 false）
- true 时：MCP initialize 响应里附 digest 内容（或首条注入消息）——Agent 开场即有关键上下文
- **默认显式调用**：避免每次连接拉摘要的 token 开销；注入是可选增强
- **验收**：开关 true → initialize 含 digest；false → 不含

### G8 — 回归 + 测试矩阵

- `pytest tests/` 全绿（6 核心接口零破坏）
- 新测试 `tests/test_digest.py`：见 §5.2 矩阵

---

## 4. 执行顺序

```
G1 → G2 → G3 → G4 → G5 → G6 → G7 → G8
（G2 是三块核心；G3/G4 是状态机；G5 暴露；G6 指针；G7 可选）
```
**分批 commit**：① G1-G2（构建核心 + 单测）② G3-G4（dirty + 生命周期）③ G5-G6（工具 + 可逆压缩）④ G7（注入，可选）⑤ G8（回归）

---

## 5. 验收标准

### 5.1 功能
1. 摘要三块正确（身份 / 近期关键 / 进行中）
2. dirty 触发 → 增量重建；无 dirty → 缓存
3. digest chunk 双时态 supersede，`asof` 可回放历史摘要
4. `memory_get_digest` 双模式（摘要 / 展开）
5. 可逆压缩：截断行可经指针取回原文
6. 信息单源：摘要行信息 chunk 全有

### 5.2 测试矩阵

| 场景 | 期望 |
|---|---|
| 写 identity_fact → get_digest | 摘要含"身份"行 + ref 指向该实体 |
| 写 decision(importance=0.9) → get_digest | 摘要含该行 + dirty 清零 |
| 写 fact(importance=0.5) → get_digest | **不触发 dirty**（非高 importance） |
| 连续两次 dirty 重建 | 旧 digest valid_until 置位 + superseded_by |
| `get_digest(ref=<行>)` | 返回该行源 chunk 完整内容 |
| 2000 字超限 | truncated=True + 截断行仍可展开 |
| 源 chunk update 后重建 | 指针指向新版本 |
| `asof` 回放 | 能取回历史 digest |

---

## 6. 风险与边界

| 风险 | 缓解 |
|---|---|
| **摘要误导**（Agent 依赖压缩版忽略细节） | 可逆压缩：需要细节显式展开；信息单源；摘要仅"开场提示"不是权威 |
| **dirty 漏触发**（写了高 importance decision 但没置位） | remember() 挂钩（G3）；漏了最多缓存旧摘要，下次写入纠正 |
| **digest chunk 被 P1a 重新分类** | classify 跳过 `source='digest'`（§1.1）；digest 固定 memory_type='fact' |
| **指针漂移**（源 chunk 被 update/forget） | G6 指针生命周期：重建刷新 + 版本链可追溯 |
| **token 开销**（每次连接注入摘要） | `inject_on_initialize` 默认 false（显式调用）；注入是可选 |
| **与 recall 的关系** | 摘要 ≠ 召回——摘要是"主动给"，召回是"按需找"；二者互补不替代 |

---

## 7. 参考
- `docs/DESIGN.md` §4.5（摘要）/ §4.5.1（生成刷新）/ §4.5.2（可逆压缩 v0.13）/ §3.0.1（信息单源）/ §5.7（工具）
- P1a 分类器（`classify.py`，digest 块2 依赖其 memory_type）
- `docs/TASKS_L2_EXTRACT.md`（P1a，前置）
- identity_fact 模式（`scripts/identity_fact_manager.py`，块1 来源）
