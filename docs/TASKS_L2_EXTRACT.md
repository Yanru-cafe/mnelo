# 任务分解：P1a 记忆类型规则分类器（TASKS_L2_EXTRACT）

> **给 hermes 的执行指南**。实现 DESIGN §5.2 **P1a（规则提取）**的第一步——**memory_type 规则分类器**，解决实际发现的"4344/4344 chunks 100% fact，6 类系统空架子"问题（v0.3 报告 §2）。
> **语言覆盖**：简体中文 + **繁体中文** + 英文。
> **LLM 定位**：本任务**零 LLM**（P1a）；P1b（LLM 语义分类）是后续阶段，本任务只预留接口。
> **前置**：读 `docs/DESIGN.md` §3.0（六类类型谱系 + 生命周期表）、§5.2（P1a/P1b 分界）、§5.5（规则/LLM 矩阵）。
> **时间窗**：可独立交付，不依赖 H0/H1（写路径集成不需要 audit_log）。

---

## 0. 背景与目标

**问题**：`remember()` 默认 `memory_type='fact'`，调用方（Hermes agent 对话，68% 来源）从不传类型 → **实际 0 个非 fact**。6 套 TTL（§3.0.5）+ 6 套矛盾规则 + H3 卫生全部空转。

**目标**：让新写入的 chunk 在**写路径**就获得合理类型（规则判断），存量 chunk 通过**回填脚本**升级。把类型使用率从 0% 拉到可用的程度，激活按类型的 TTL/矛盾/卫生。

**原则（§5.5 "宁缺毋滥"）**：
- **命中强标记才分类**；模糊/无标记 → **保持 fact**（留给 P1b LLM）
- 高精度优先，召回其次——错误的类型比没类型更有害（会套错 TTL/矛盾规则）

---

## 1. 契约

### 1.1 分类器接口（新模块 `classify.py`）

```python
def classify_memory_type(text: str) -> Optional[str]:
    """P1a 规则分类 → 返回 memory_type 或 None (保持 fact).

    - 先做 繁→简 归一化 (§1.2), 再做 简体+英文 标记匹配
    - 命中强标记 → 返回对应类型
    - 无命中/弱标记 → 返回 None (调用方保持默认 fact)
    """
```

### 1.2 繁→简字符归一化（关键技术决策）

**定案：维护一个 bounded 的 `_T2S` 字符映射 dict + 单一简体标记集**（不是双份标记表）。

```python
# classify.py
_T2S = {
    "覺": "觉", "歡": "欢", "買": "买", "賣": "卖", "時": "时", "點": "点",
    "倉": "仓", "減": "减", "驟": "骤", "暫": "暂", "訂": "订", "佔": "占",
    "後": "后", "於": "于", "較": "较", "擇": "择", "標": "标", "計": "计",
    "劃": "划", "務": "务", "應": "应", "會": "会", "個": "个", "這": "这",
    "麼": "么", "說": "说", "讓": "让", "種": "种", "樣": "样", "對": "对",
    "現": "现", "們": "们", "為": "为", "與": "与", "從": "从", "來": "来",
    "價": "价", "倉位": "仓位", "買入": "买入", "賣出": "卖出",
    # 实现时按标记集实际出现的字符扩展（bounded, 约 50-80 字）
}

def _normalize(text: str) -> str:
    return "".join(_T2S.get(ch, ch) for ch in text)
```

- **为什么不是双份标记表**：简体/繁体标记逐一成对维护会翻倍且易漏（漏一个繁体变体就静默 miss）；归一化后**标记集单一事实源**，扩展只需加简体
- **为什么不用 opencc**：零依赖优先（mnelo 无 opencc 依赖）；标记集字符是 bounded 的（约 50-80 个常用字），手写 dict 够用且透明（boring & predictable）
- **若将来字符集膨胀**（标记扩到几百字）再评估 opencc——本任务固定手写 dict

### 1.3 标记集组织（数据，非硬编码）

标记集放**模块级常量表**（`classify.py` 顶部），不写死进逻辑——方便扩展双语/更多标记：

```python
_MARKERS: Dict[str, Dict[str, List[str]]] = {
    "preference": {"cn": [...], "en": [...]},
    "decision":   {"cn": [...], "en": [...]},
    "episode":    {"cn_time": [...], "cn_action": [...], "en_time": [...], "en_action": [...]},  # 复合规则
    "procedure":  {"cn": [...], "en": [...]},
    "ephemeral":  {"cn": [...], "en": [...]},
}
```

### 1.4 集成点

| 位置 | 改动 | 说明 |
|---|---|---|
| **写路径** `memory.py remember()` | 显式类型 > 规则分类 > fact | 调用方显式传类型 → 用显式；否则跑规则；无命中 → fact |
| **MCP** `mcp_server.py memory_remember` | `memory_type` 默认 None → 服务端跑分类器 | 现在默认 'fact'，改为 None（触发分类） |
| **回填** `scripts/backfill_memory_type.py` | 遍历 `memory_type='fact'` 的 chunks，规则分类，直接 UPDATE | 存量 4344 升级；确定性操作，无需审计（L2 H0 落地后改走提案链） |

---

## 2. 任务清单

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **E1** | `classify.py` 骨架 + 繁→简归一化 | — | 归一化单测 |
| **E2** | 六类标记表（CN + EN，含 episode 复合规则） | E1 | 标记覆盖双语 |
| **E3** | `classify_memory_type()` 匹配逻辑（强标记→类型，模糊→None） | E1-E2 | 双语+繁简单测 |
| **E4** | 写路径集成（remember + mcp_server 默认值） | E3 | 新写入自动分类 |
| **E5** | 回填脚本 `backfill_memory_type.py` | E3 | 存量升级 |
| **E6** | 全量回归 + 双语/繁简测试矩阵 | E4-E5 | 全绿 + 0 误伤 |

---

## 3. 任务详述

### E1 — `classify.py` 骨架 + 繁→简归一化

- 新模块 `classify.py`（放 repo 根，与 search_index.py 同层）
- `_T2S` 字符映射（§1.2）+ `_normalize()`
- `_MARKERS` 常量表结构（§1.3）
- **验收单测**：`_normalize("我覺得這個方案不錯") == "我觉得这个方案不错"`；空串/纯英文不报错

### E2 — 标记表（核心，seed 集）

> 标记是 **seed**，实施时按实际数据校准。**强标记** = 命中即分类；**弱标记** = 不单独分类（保持 fact）。

| memory_type | CN 强标记（归一化后） | EN 强标记 | 说明 |
|---|---|---|---|
| **preference** | 偏好, 我喜欢, 更喜欢, 比较喜欢, 倾向于, 希望, 想要 | prefer, prefer to, i like, i'd like, favorite, would rather, like to | 带主语（我）更准；裸"喜欢"弱 |
| **decision** | 决定, 决定不, 打算, 计划, 目标是, 我的判断是, 我选择 | decided, decision, plan to, decided to, my call is | "因为…所以"弱（常见于事实陈述，不标） |
| **episode** | 时间锚 + 动作（复合规则）：cn_time={今天,昨天,前天,本周,上周,月日} cn_action={建仓,买入,卖出,清仓,加仓,减仓,买了,卖了} | time={yesterday,today,on <date>,last week} action={bought,sold,added,reduced,closed} | **必须时间+动作都命中**才标 episode |
| **procedure** | 步骤, 流程, 方法, 怎么, 如何, 通常这样, 每次都是, 模板, 规范 | steps, how to, process, workflow, procedure, template, convention | |
| **ephemeral** | 临时, 草稿, 待定, 暂定, 占位, 稍后 | draft, temp, temporary, placeholder, wip, tbd, todo | |
| **fact** | — | — | 默认；无强标记命中即 fact |

**优先冲突**：文本同时命中多类强标记（如"我决定明天建仓"=decision+episode）→ **优先级 decision > episode > 其余**（决策优先，理由：决策常伴随事件，反向少）。

### E3 — 匹配逻辑

```python
def classify_memory_type(text: str) -> Optional[str]:
    norm = _normalize(text)
    lower = norm.lower()
    # episode: 复合规则 (时间 AND 动作都命中)
    if any(t in norm for t in _MARKERS["episode"]["cn_time"]) and \
       any(a in norm for a in _MARKERS["episode"]["cn_action"]):
        return "episode"
    if any(t in lower for t in _MARKERS["episode"]["en_time"]) and \
       any(a in lower for a in _MARKERS["episode"]["en_action"]):
        return "episode"
    # 单标记类: 优先级 decision > preference > procedure > ephemeral
    for t in ("decision", "preference", "procedure", "ephemeral"):
        if any(m in norm for m in _MARKERS[t]["cn"]) or \
           any(m in lower for m in _MARKERS[t]["en"]):
            return t
    return None
```

- **弱标记**（如裸"喜欢"、"因为"）**不进强标记集**——模糊保持 fact（§5.5 宁缺毋滥）
- **验收单测**：见 §5.2 双语+繁简矩阵

### E4 — 写路径集成

- `memory.py remember()`：
  ```python
  if memory_type == "fact":   # 调用方未显式指定（默认）
      from classify import classify_memory_type
      inferred = classify_memory_type(content)
      if inferred:
          memory_type = inferred
  ```
- `mcp_server.py memory_remember`：`memory_type` 默认 `"fact"` → 改为 `None`（None 触发分类；显式传值尊重调用方）
- **行为**：调用方显式类型 > 规则分类 > fact
- **验收**：remember("我偏好简洁日报") → chunk.memory_type == "preference"；remember("记录今天建仓了 sh600089") → "episode"；显式传 "fact" → 保持 fact（不被规则覆盖）

### E5 — 回填脚本

```python
# scripts/backfill_memory_type.py [--dry-run] [--limit N]
# 遍历 chunks WHERE memory_type='fact' AND valid_until IS NULL
# → classify_memory_type(content) → 命中则 UPDATE chunks SET memory_type
# --dry-run 只报数
```
- **确定性操作，直接 UPDATE**（非 LLM，无审计需求；H0 落地后 L2 分类走提案链，本脚本是存量一次性迁移）
- **验收**：--dry-run 报将分类数；实际跑后 `SELECT memory_type, COUNT(*) GROUP BY` 非 fact 占比上升

### E6 — 回归 + 测试矩阵

- `pytest tests/` 全绿（默认路径零破坏）
- 新测试 `tests/test_classify.py`：见 §5.2 矩阵

---

## 4. 执行顺序

```
E1 → E2 → E3 → E4 → E5 → E6
（E1-E3 是分类器核心；E4 写路径；E5 回填；E6 回归）
```
**分批 commit**：① E1-E3（classify.py 核心 + 单测）② E4（写路径集成）③ E5（回填脚本）④ E6（回归）

---

## 5. 验收标准

### 5.1 功能
1. 写路径：新写入 chunk 自动分类（显式类型 > 规则 > fact）
2. 回填：存量 4344 中非 fact 占比从 0% 上升
3. 确定性：同一文本两次分类结果一致
4. 零误伤：6 个核心接口（recall/remember/forget/update/graph_query/stats）行为不变

### 5.2 双语 + 繁简测试矩阵（E3 验收）

| 输入（简体） | 输入（繁体） | 输入（EN） | 期望 |
|---|---|---|---|
| 我偏好简洁日报 | 我偏好簡潔日報 | I prefer the concise report | preference |
| 我决定明天减仓 | 我決定明天減倉 | I decided to reduce tomorrow | decision |
| 今天建仓了 sh600089 | 今天建倉了 sh600089 | Bought 100 shares of sh600089 today | episode |
| 记录一下做周报的步骤 | 記錄一下做週報的步驟 | Here are the steps for the weekly report | procedure |
| 临时草稿，稍后处理 | 臨時草稿，稍後處理 | temp draft, handle later | ephemeral |
| 这是一段普通记录 | 這是一段普通記錄 | This is a normal note | **fact**（无强标记） |
| 我决定今天建仓 | 我決定今天建倉 | I decided to buy today | **decision**（优先级 decision>episode） |
| 用户喜欢这个方案（第三人称） | 用戶喜歡這個方案 | The user likes this plan | **fact**（无"我"主语，弱） |

- 繁体用例必须**先归一化再匹配**（`_normalize` 命中）
- "用户喜欢"（第三人称）→ fact：**带"我"主语才算 preference**，防误标他人偏好

---

## 6. 风险与边界

| 风险 | 缓解 |
|---|---|
| **误分类**（套错 TTL/矛盾规则） | 强标记才分类；第三人称偏好不标；episode 需时间+动作复合；优先级 decision>episode |
| **繁→简映射漏字** | 标记集字符 bounded；实施时扫描标记集实际出现的繁体字补全 `_T2S`；测试矩阵覆盖 |
| **写路径行为变化** | 确定性、无 LLM；显式传类型永远优先；E6 回归兜底 |
| **回填大量 UPDATE** | `--dry-run` + `--limit`；分批 |
| **与 L2 提案链冲突**（H0 后分类走 audit） | 本任务写路径是确定性规则（非 LLM 判断），可直接落地；H0 后 L2 分类走提案链，写路径规则保留为"写时默认" |

---

## 7. 参考
- `docs/DESIGN.md` §3.0（六类谱系 + 生命周期表）/ §3.0.5 / §5.2（P1a/P1b 分界）/ §5.5（规则/LLM 矩阵）
- v0.3 实际报告 §2（memory_type 100% fact 问题）
- `docs/TASKS_L2_HYGIENE.md` H3（TTL 按类型——分类器激活它）
- `docs/TASKS_H1_SCHEMA.md`（后续 L2 分类走 audit 链的 schema 基础）
