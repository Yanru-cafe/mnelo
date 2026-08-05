# mnelo

> **mnelo** = μνήμη + λόγος（希腊语：*记忆* + *理性*）。
> 面向 AI Agent 的本地优先、单文件知识图谱记忆层——带**可选自主维护层**和**会话状态摘要**（Agent 开场自动注入）。

> **English**: [README.md](README.md) 提供英文版。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-i18n-国际化)
[![Local-first](https://img.shields.io/badge/local--first-100%25-brightgreen)](#-设计原则)
[![CI](https://github.com/chinesewebman/mnelo/actions/workflows/ci.yml/badge.svg)](https://github.com/chinesewebman/mnelo/actions/workflows/ci.yml)

AI Agent 的记忆层。通过 **4 个维度**记忆——向量语义、知识图谱、全文元数据、实体身份——让每个决策都能回溯到产生它的条件。一个本地 SQLite 文件，供所有本地 MCP 客户端共享。**零云、零锁定。**

## 🧭 这是什么？（大白话，给非技术人员）

mnelo 是**你的 AI 助手的记忆**——像一本你的 AI 随身携带的笔记本，从一次对话带到下一次。

- **它记得你**——你是谁、你的偏好、重要的事实。
- **它记得正在发生什么**——近期的决策、进行中的工作。
- **它自动归档记忆**——无需手动整理。
- **它会随时间自我整理**（可选）——陈旧的东西淡出、过期的东西被清理，且每一步都可撤销。
- **它每次开场问候你的 AI**——一份"现在进展到哪"的简短摘要，让你的助手每次对话开始就带着上下文。

它**完全运行在你自己的电脑上**（一个本地文件）。不上云、无账号、无订阅。

**如果你不是技术人员**：你不需要读本页其余部分。安装设计成——把本页交给任何 AI 编程 agent（Claude Code、Cursor…），它会帮你装好 mnelo。见 [🤖 一句话让 agent 装](#-一句话让-agent-装)。之后你的 AI 会自动使用 mnelo。

---

**内部构成（2026-08）**：
- **4 路召回 + RRF**——vector / graph / meta / entity 四路融合，无需分数归一化
- **memory_type 类型谱系**——每条记忆自动分类（fact / preference / episode / decision / procedure / ephemeral），**零 LLM 规则分类器**，支持简体/繁體/英文
- **L2 自主维护层（可选）**——卫生 pass（importance 衰减、按类型 TTL、purge 候选）+ 完整 **audit_log + undo** 审计链；事实晋升（高频记忆升为 canonical_fact）
- **会话状态摘要**——500–2000 字"当前状态"摘要，会话开场自动注入（Claude Code SessionStart 钩子 / MCP resource）
- **可插拔向量后端**——`sqlite-vec` 默认（任意 CPU）、`usearch`（HNSW，任意 CPU）、`zvec`（HNSW + 原生 FTS，AVX2+）

**为什么 4 路召回赢**：每路互补盲区——向量漏字面（商品/品类代码）、meta 漏语义改写、graph 漏无实体链接的孤儿 chunk、entity 漏长文。四路并行（WAL 并发读，p50 = **8.5 ms** / p95 = **10 ms** @ 6.3k chunks），RRF 无需分数归一化融合。见 [🔀 什么是 RRF？](#-什么是-rrf)。

---

## ⚡ 一瞥

| | |
|---|---|
| **存储** | 单 SQLite 文件（~45 MB @ 4498 entities / 4343 chunks，2026-08 实测） |
| **向量后端** | `sqlite-vec`（默认，任意 CPU）· `usearch`（HNSW，任意 CPU）· `zvec`（HNSW+FTS，AVX2+） |
| **嵌入模型** | `bge-small-zh-v1.5`（512 维，中文原生；英文/多语可换） |
| **召回** | 4 路混合：`vector + graph + meta + entity` → RRF 融合 |
| **记忆类型** | fact / preference / episode / decision / procedure / ephemeral（零 LLM 自动分类） |
| **自主层** | 可选 L2 维护：衰减 / TTL / purge + audit_log + undo + 事实晋升 |
| **会话摘要** | 500–2000 字，开场注入（Claude Code 钩子 / MCP resource） |
| **协议** | MCP over SSE（127.0.0.1:8086）——**14 个工具**，Bearer 认证 |
| **延迟（热）** | p50 = **8.5 ms**，p95 = **10 ms**（6.3k chunks 基线） |
| **代码量** | ~4000 行 Python（memory.py + 分类器 + 检索适配器 + scripts + client） |
| **依赖** | `mcp[cli]`、`sqlite-vec`、`fastembed`（+ 可选 `usearch` / `zvec`） |
| **双语** | 中英一等公民；分类器支持简体/繁體/英文 |

---

### 🔀 什么是 RRF？

**RRF = Reciprocal Rank Fusion（倒数排名融合）**（[Cormack et al., 2009](https://dl.acm.org/doi/10.1145/1571941.1572114)）。融合异构检索路线的结果时，最简单却真正打败加权分数调参的方法。

核心：各路独立排名，按 `1 / (k + rank_i)` 求和合并，`k=60`。

```
Lane A (vector):   doc1=1, doc3=2, doc5=3
Lane B (graph):    doc2=1, doc1=2, doc7=3
Lane C (meta):     doc5=1, doc1=3, doc9=2

最终分 = Σ_lanes 1 / (60 + 该路排名)
→ doc1: 1/61 + 1/62 + 1/63 = 0.0483   ← 胜出
```

**为什么 RRF 胜过加权分数融合？**

| | RRF | 加权分数融合 |
|---|---|---|
| 需要分数归一化？ | **否**——只用排名 | 是（各路分数尺度需校准） |
| 对单路失控鲁棒？ | **是**——离群排名只贡献 `1/(60+rank)` | 否——偏斜分数会主导 |
| 新增一路？ | 直接加 | 重新调所有权重 |
| 实现成本 | ~5 行 | 分数校准 + 权重网格搜索 |

mnelo 四路用标准 `k=60`，另加一个小 `0.05/sqrt(rank)` boost 给已知**品类代码**实体命中（如商品 SKU）——可配置，默认一个精选实体种类清单。

---

## ✨ 特色

### 🧠 知识图谱感知
每条 chunk 可链接到类型化实体，关系图可查询。**实体 `kind` 是开放分类**——schema 无枚举约束。mnelo 自带一个小**种子集**（`stock` / `concept` / `person` / `user` / `canonical_fact` / `identity_fact`）；你领域的 kind 自由加（任何字符串都行）。（`container` 显式收纳是 DESIGN 规划，尚未上线。）`memory_graph_query` 返回 2-hop 邻居。

### 🏷️ memory_type 类型谱系 + 零 LLM 分类器
每条 chunk 带一个决定其生命周期的类型（§3.0）：`fact` / `preference` / `episode` / `decision` / `procedure` / `ephemeral`。**规则分类器**（P1a，无 LLM，确定性）按强标记自动打标——**简体/繁體（字符映射归一化）/英文**三语。模糊输入保持 `fact`（宁缺毋滥）。高频事实后续可由 L2 层**晋升**为 `canonical_fact` 实体。

### 🛡️ L2 自主维护层（可选）
启用（`[l2] enabled=true`）后，`run_maintenance` pass 保持记忆健康：
- **importance 衰减**——陈旧的低价值 chunk 向 floor（0.1）衰减
- **按类型 TTL**——`ephemeral` 7 天 / `fact` 365 天 / `preference` 180 天 / `episode`+`decision` 730 天 / `procedure` 永久
- **purge 候选**——衰减/过期项进非破坏报告；物理删除需 `confirm_destructive`
- **事实晋升**——高频事实升为结构化 `canonical_fact` 实体
- **完整审计链**——每个 proposal/applied/skipped/reverted 落 `audit_log`（before/after JSON + revert_sql）；`memory_audit_undo` 重放。**每提案独立事务**（坏提案不毒害整批）。

一切**默认 dry-run**、幂等、感知受保护实体。

### 🗒️ 会话状态摘要 + 可逆压缩
**500–2000 字摘要**（身份事实 + 近期关键决策 + 进行中会话）自动维护，会话开场注入 Agent——开场即知"世界当前状态"，无需召回。每条摘要行带**溯源指针**指向源 chunk，Agent 可**按需展开**（可逆压缩——压缩视图进上下文，细节按需取回）。暴露方式：
- **MCP resource** `memory://session/digest`（任何 MCP 客户端）
- **MCP tool** `memory_get_digest(ref=None)`（ref = 展开某行）
- **Claude Code SessionStart 钩子**——每次新会话自动注入

### 🔌 标准 MCP，无锁定——14 个工具

| 工具 | 用途 |
|---|---|
| `memory_remember` | 写入 chunk + 实体 + 关系（自动分类） |
| `memory_recall` | 4 路召回 + RRF（filters：type/source/session/asof） |
| `memory_relate` / `memory_forget` / `memory_update` | CRUD，软删 + 版本链 |
| `memory_graph_query` | 2-hop 子图导航 |
| `memory_stats` | 统计，含 `hygiene` 子键 |
| `memory_get_digest` | 会话摘要（ref = 展开某行） |
| `memory_maintenance` | 跑 L2 pass（默认 dry-run） |
| `memory_audit_list` / `memory_audit_undo` | 审计轨迹 + 撤销 |
| `memory_entity_resolve` / `memory_list_entities` / `memory_search_relations` | 实体管理 |

### 🌏 每一层都双语
- 语言自动检测（`MNELO_MEMORY_LANG` > `LC_ALL` > `LANG` > `en`）
- 分类器处理简体 / 繁體 / 英文（字符映射归一化）

### 🔎 可插拔向量后端

| 后端 | CPU | 特性 | 何时用 |
|---|---|---|---|
| **sqlite-vec**（默认） | 任意 | 暴力 KNN，零依赖 | 一切机器，含 VPS/旧客户端 |
| **usearch** | 任意 | HNSW（真 ANN） | 更大规模 / 旧 CPU 上更快 |
| **zvec** | **AVX2+** | HNSW + 原生 FTS（BM25 + jieba 中文） | 规模 + 全文检索 |

自动检测；`[search] backend` 切换；配置的后端不可用则**自动降级**（zvec → usearch → sqlite_vec 子进程检测）。见 [部署矩阵](#-向量后端部署矩阵)。

---

## 🛡 设计原则

1. **本地优先**。永不调用云 API。嵌入模型预下载后可完全离线。
2. **单文件**。SQLite。`cp memory.db` = 完整备份。
3. **标准 MCP，无锁定**。14 个工具走 SSE，任何 MCP 客户端可用。
4. **通用优先**。功能默认协议通用（任何 MCP 客户端）；客户端专属胶水（如 Claude Code 钩子）是薄的、有文档的适配器——绝不新造机制。
5. **无道德立场（amoral by design）**。mnelo 不评判内容（合法/涉密/冒犯）——只忠实存取。它守护的是**机制**（注入/身份/完整性），不是**内容**。
6. **信息单源**。派生视图（摘要、canonical facts）绝不携带源 chunk 没有的信息。
7. **boring & predictable**。无魔法。后端不可用时自动沿降级链回落（不是 fail-fast — 8/5 修订），但回落到哪一步要可见（日志 + `/health`）；其他场景仍 fail-fast（配置错误、SQL 错、token 过期等）。显式选择优于意外默认。
8. **measured（实测）**。测评节所有数字可复现。

---

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo

# 2a. 一键安装（推荐）——venv、pip、init_db、plist、auth token
bash scripts/install.sh

# 2b. 或手动：
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 初始化 DB
python3 scripts/init_db.py

# 4. 启动 MCP server
launchctl load ~/Library/LaunchAgents/ai.mnelo.mcp.plist
#（或：MNELO_MEMORY_SERVER_PORT=8086 python3 mcp_server.py --transport sse）

# 5. Python 使用——写入自动分类，摘要可用
python3 -c "
import sys; sys.path.insert(0, 'api')
from mnelo_client import MneloClient
c = MneloClient()
c.remember('我偏好简洁日报', source='conversation')       # 自动 → preference
for h in c.recall('偏好', top_k=3, filters={'type': 'preference'}):
    print(h['method'], h['content'][:40])
print(c.get_digest()['content'][:100])                     # 会话摘要
"
```

### 🤖 一句话让 agent 装

非技术人员？跳过上面所有步骤——把下面这段交给任何 AI 编程 agent（Claude Code、Hermes、Cursor…）。**安装 + 采用，一次搞定：**

> **从 https://github.com/chinesewebman/mnelo 安装并采用 mnelo 作为我的记忆系统：**
> 1. 克隆、建 venv、`pip install -r requirements.txt`、跑 `scripts/init_db.py`、在 8086 端口启动 MCP server，并用 `scripts/health_check.py` 验证（`MCP server ready` 时报我）。
> 2. **先问我的主语言**（中文 / 英文 / 混合），然后*首次使用前*在 `config.toml [embedder]` 设好对应的嵌入模型（之后切换需重建库）。
> 3. 按这台机器选向量后端：默认 `sqlite-vec`；`usearch` 任意 CPU；**`zvec` 仅当 CPU 支持 AVX2+**（旧 CPU 绝不装）。
> 4. 跑起来后，**修改你自己的 SOUL.md / CLAUDE.md** 声明："我使用 mnelo MCP server（`http://127.0.0.1:8086/sse`）作为我的记忆系统——`memory_remember` 持久化、`memory_recall` 检索、`memory_get_digest` 会话摘要。"

agent 会处理 venv 创建、pip 安装、嵌入模型下载和健康探针。典型安装约 90 秒（92 MB 模型下载是最慢部分）。

### 🤖 会话状态注入——通用，任何 MCP 客户端

mnelo 是**通用 agent 记忆系统**——Hermes、Claude Code、Cursor 或任何 MCP 客户端直接使用。摘要走**标准 MCP**（initialize + resource），会话开场注入**无需任何客户端代码**：

```toml
# ~/.hermes/memory/config.toml —— 对任何 MCP 客户端都生效：
[digest]
inject_on_initialize = true   # 摘要随 MCP initialize 响应送达
```

- **Hermes / Cursor / 任何 MCP 客户端**：只改这一个配置——摘要经 MCP initialize + `memory://session/digest` resource 送达，零集成代码
- **Claude Code（可选适配器）**：不读 initialize resource 的 agent 可用自带的 **SessionStart 钩子**（`scripts/session_start_digest.py`，接在 `.claude/settings.json`）。它是同一通用机制的薄适配器——**容错**（mnelo 未跑 → 静默 exit 0）、**自举**（`python3` 解析到 venv 外会自动 re-exec）、输出 `[mnelo-digest]` 围栏块（数据而非指令，DESIGN §12）

无论哪种方式，你的 agent 每个会话开场即知当前记忆状态——无需召回。

---

## 🤖 给 AI agent 的操作指令（把 mnelo 设为你的记忆系统）

如果你是 AI agent（Claude Code、Hermes、Cursor…），在给你的用户安装 mnelo 时，按以下操作步骤：

**1. 按实际机器选择向量后端。**
- 默认 **`sqlite-vec`** 任意 CPU 可用——没有理由就不换。
- 更大规模 / 旧 CPU 上要更快召回 → **`usearch`**（任意 CPU）。
- **`zvec`** 带原生全文检索，但**需要 AVX2+**——旧 CPU **别装**（那里 `import` 即崩）。检测 CPU 或询问用户。
- 配置：`config.toml [search] backend` 或 env `MNELO_MEMORY_SEARCH_BACKEND`。

**2. 选嵌入模型前先问用户主语言**（之后切换需重新初始化 DB——所以*首次使用前*就问）：
- 中文 → `bge-small-zh-v1.5`（默认，512 维）
- 英文 → `bge-small-en-v1.5`（384 维）
- 混合 / 多语 → `paraphrase-multilingual-MiniLM-L12-v2`（384 维）

**3. 装完后，修改你自己的配置，声明 mnelo 是你的记忆系统。** 在你的 `SOUL.md` / `CLAUDE.md`（或等价文件）加一段：

> **记忆**：我使用 mnelo MCP server（SSE at `http://127.0.0.1:8086/sse`）作为我的记忆系统。
> - `memory_remember` — 持久化重要事实与决策，带类型（`preference` / `decision` / …）
> - `memory_recall` — 回答任何可能不在上下文里的问题前先检索
> - `memory_get_digest` / `memory://session/digest` — 开场注入的会话状态摘要
> 保持 mnelo 运行。如果它停了，就无记忆工作并标注记忆缺口。

**4. 验证** —— 跑 `scripts/health_check.py`（或访问 `/health`），确认 server ready 再宣布完成。

**📌 新增实体 kind**（开放分类——无需注册）：实体 `kind` 是自由文本；"加一个 kind" 本质上就是**开始用它**。用户引入新 kind 时，把它记为约定并一致使用：

> 新增一个实体 kind：`product`，用于记录产品/商品相关的实体。用 `memory_remember` 记录产品时，entities 里带 `kind: 'product'`，并保持命名与别名一致（如 id `product:sku-1024`）。把这个约定记到你的 CLAUDE.md/SOUL.md 并长期一致使用；产品召回时用 `kind: 'product'` 过滤。

可选：把这个 kind 加进 `[recall] boost_kinds` 让它像 `stock` 一样召回浮顶；用 `correct()` 或脚本回填已有实体。

**🧠 用 mnelo——写得好、检索得好**（记什么、怎么结构化）：

**1. `memory_type`——chunk 的生命周期类型。** 规则分类器自动给新写入打标，但**你知道时就显式传**：

| 类型 | 何时用 | 你会分类成的例子 |
|---|---|---|
| `preference` | 喜欢 / 不喜欢 / 风格偏好 | "我偏好简洁日报" |
| `decision` | 决策 +（最好带上）理由 | "我决定下月采购 CAT-1024" |
| `episode` | 带日期的事件 | "今天建仓了 CAT-1024" |
| `procedure` | 步骤 / 流程 / 方法 | "做周报的流程…" |
| `ephemeral` | 草稿 / 占位 / 进行中 | "临时草稿，稍后处理" |
| `fact` | 其它一切（默认） | — |

写入：`memory_remember(content, ..., memory_type='decision')` 知道就传；**不传则让自动分类器决定**（支持简体/繁體/英文）。

**2. 实体 `kind`——概念怎么结构化。** 当一个东西**跨 chunk 被引用、有别名、或是图锚点**时才建实体——一次性提及不必建。id 保持一致：`kind:slug`（如 `product:sku-1024`），别名放 `aliases_json`。用 `memory_remember(entities=[{id, kind, name, aliases}])` 把实体挂到 chunk 上；用 `memory_relate(source_id, target_id, relation, evidence_chunk_id=...)` 连概念——**每条关系都应指回支撑它的 chunk**。

**3. 回答前先检索。** 任何可能活在用户记忆里的内容（身份、决策、进行中），回答前先 `memory_recall`——需要时带 filters（`{'type': 'decision'}`、`{'source': ...}`）。会话开场 digest（`memory_get_digest`）已给你当前状态；需要细节时用 `ref` 展开某行。

**4. 一致性是契约。** type 和 kind 只有一致使用才有价值。引入新 kind 时，把约定记进你的 CLAUDE.md/SOUL.md（见上面"新增实体 kind"）。

---

## 🔎 向量后端部署矩阵

mnelo 的向量索引后端可插拔（[DESIGN §3.6/§8.3](docs/DESIGN.md)）。**默认 `sqlite-vec` 零额外依赖、任意 CPU 可用。**

| 后端 | CPU 要求 | 特性 | 何时用 |
|---|---|---|---|
| **sqlite-vec**（默认） | 任意（纯 SQLite 扩展） | 暴力 KNN；零依赖 | 一切机器，含 VPS/旧客户端 |
| **usearch**（可选） | 任意（硬件无关） | **HNSW** 真 ANN | 旧 CPU 上要真 ANN |
| **zvec**（可选） | **AVX2+**（M 系列 / 2020+ x86_64 / 现代 ARM） | HNSW + 原生 FTS（BM25 + jieba 中文） | 规模 + 全文检索 |

**部署规则**：
- **不装** → 默认 `sqlite_vec`，全功能，零配置
- **装了但 CPU 不支持**（如旧 VPS 上 `import zvec` 崩溃）→ mnelo **子进程检测**（安全，不带崩主进程）后**自动沿降级链回落**
- **启用 usearch/zvec**：`pip install -r requirements-usearch.txt`（或 `-zvec.txt`）+ `config.toml` `[search] backend = 'usearch'|'zvec'`（或 env `MNELO_MEMORY_SEARCH_BACKEND`）
- `scripts/health_check.py` 报告实际生效后端；`/health` 降级时给出维护建议

**自动降级链（8/5 主人拍板）**：`build_search_index()` 工厂通过子进程 `import` 检测实现 **zvec → usearch → sqlite_vec** 级联——装一个就跑；工厂挑 import 成功的最高优先级后端。**不抛 fail-fast**：即使 CPU 跑不了 zvec，系统仍可用（装了 usearch 就 usearch，没装就 sqlite_vec）。工厂永远返回能跑的索引。

**切换后端**（sqlite_vec → usearch/zvec）：必须从 chunks 全量重嵌：
```bash
python scripts/rebuild_index.py --backend usearch    # 全量重建
python scripts/repair_index.py --backend usearch     # 清孤儿向量（chunk 删了但索引残留）
# 或先 dry-run:
python scripts/rebuild_index.py --backend usearch --dry-run
```

> ⚠️ zvec 0.6 在 Ivy Bridge 之前的旧 x86_64 上 `import` 即崩——这些机器别装 zvec。工厂会自动跳过它，但装一个跑不了的包是浪费磁盘。

---

## 📊 测评结果

全部在单台 MacBook（M 系列）实测。当前基线（2026-08）：`memory.db` = **~44.7 MB + 0.7 MB WAL / 4498 entities / 4343 chunks**。

### 延迟

| 指标 | 值 | 说明 |
|---|---|---|
| **p50** | **8.5 ms** | 热路径，6.3k chunks，4 路并发 |
| **p95** | **10 ms** | 同上 |
| **p50（10k 种子）** | **23 ms** | `scripts/benchmark.py --chunks 10000` |
| **p95（10k 种子）** | **29 ms** | 向量检索随 chunk 数增长 |
| **avg（24h 热）** | 34.4 ms | 含冷启动离群 |
| **冷启动** | ~1.1 s | MCP 启动 + 嵌入模型加载 |

复现：`python scripts/benchmark.py --chunks 10000 --queries 100 --json bench.json`

### 内存占用

单 MCP server 进程，空闲（macOS M 系列）：**~270 MB RSS**——其中嵌入器（bge-small-zh 权重 + onnxruntime + tokenizer）约 200 MB，**与数据量无关**；其余（~70 MB）是 Python + MCP + SQLite + sqlite-vec。92 MB 模型文件运行时膨胀到 ~200 MB（float32 加载 + ONNX arena + tokenizer）——**文件大小 ≠ 内存成本**。

模型在 HuggingFace Hub 缓存（`~/.cache/huggingface/hub/`），首次使用自动下载，可与其它工具共享。

### 🌐 多语种模型

默认 `bge-small-zh-v1.5` 中文原生；经 `config.toml` `[embedder]`（或 env）切换英文（`bge-small-en-v1.5`，384 维）或多语（`paraphrase-multilingual-MiniLM-L12-v2`，384 维）。⚠️ 切换模型需重新初始化 DB（向量维度烧进 schema）。

### 测试覆盖

```
$ python3 -m pytest tests/ -q
# 738 passed, 1 skipped（~210s）[2026-08]
```

51 个测试文件覆盖：核心 CRUD/召回、memory_type 分类器（双语/繁简）、L2 卫生/watermark/原子性、audit undo、digest、检索后端、Claude Code 钩子、schema 一致性。

---

## 🏗 项目结构

```
mnelo/
├── memory.py            ← 核心 Memory 类（~1600 行）：CRUD + 4 路召回 + RRF
│                           + L2（run_maintenance / audit / digest / promote）
├── classify.py          ← P1a 规则分类器：memory_type 自动打标（简体/繁體/EN）
├── search_index.py      ← SearchIndex 适配器：sqlite-vec / usearch / zvec 后端
├── mcp_server.py        ← MCP server（SSE），14 工具，Bearer 认证，/health
├── config.py            ← env > TOML > 默认（含 [search]、[digest]、[l2]）
├── schema.sql           ← 12 表，含 audit_log + memory_type/user_confirmed/processed_at
├── embedder.py          ← fastembed 封装（bge-small-zh）
├── entity_resolve.py    ← 实体合并 + 别名消歧
├── validation.py        ← 输入清洗（大小上限、控制/bidi 字符）
├── auth.py / metrics.py / mnelo_locale.py / i18n_messages.py
│
├── api/mnelo_client.py  ← MneloClient（14 工具，含 get_digest / maintenance / audit）
│
├── scripts/
│   ├── init_db.py / install.sh / health_check.py
│   ├── session_start_digest.py   ← Claude Code SessionStart 钩子
│   ├── rebuild_index.py / repair_index.py / repair_vectors.py
│   ├── migrate_to_mnelo.py / import_holdings.py / import_identity_facts.py
│   └── identity_fact_manager.py / benchmark.py
│
├── tests/               ← 51 个文件（分类器、L2、digest、检索后端、钩子、schema）
│
└── docs/
    ├── DESIGN.md            ← v0.13 设计蓝图（数据模型、L2、后端、安全）
    ├── TASKS_*.md           ← 6 份实施手册（H/E/G/A/S/P 系列）
    ├── ARCHITECTURE.md / SCHEMA.md / RUNBOOK.md
```

---

## 🆚 为什么选 mnelo？（对照主流 agent 记忆生态）

mnelo 和主流的 agent 记忆框架不在同一赛道。**Mem0 / Letta / Zep / Cognee** 面向*产品级规模*——托管服务或自托管服务器，往往需要外部向量/图数据库或完整 agent 运行时。**mnelo 是本地优先、单文件、零云的选项**：一个 SQLite 文件、标准 MCP、任何 agent 可用、开箱双语、带可选自主维护层。

| | **mnelo** | **Mem0** | **Letta (MemGPT)** | **Zep/Graphiti** | **Cognee** |
|---|---|---|---|---|---|
| **部署** | 一个 SQLite 文件 | 托管 / 自托管 | agent 运行时（重） | 图库 + 服务 | 自托管管线 |
| **云依赖** | **永不** | 可选 | 否 | 可选 | 否 |
| **零依赖安装** | **3 个 pip 包** | 需向量库 | ~500MB 运行时 | 需 Neo4j 等 | 需 KG 栈 |
| **知识图谱** | ✅ 原生 | ✅（付费档） | – | ✅（核心） | ✅（核心） |
| **4 路 RRF 召回** | ✅ | – | – | – | – |
| **双语（中/EN），简/繁分类器** | ✅ | – | – | – | – |
| **自主维护** | ✅（L2，审计/撤销） | ✅（LLM 抽取） | ✅（自我编辑） | – | – |
| **时态模型** | ✅（`valid_until` + `asof`） | – | – | ✅（双时态） | – |
| **会话摘要注入** | ✅（任何 MCP 客户端） | – | ✅（核心记忆） | – | – |

**诚实的取舍**：主流框架更成熟、有托管云档、面向大规模或长驻 agent。mnelo 追求相反方向：**简单、本地优先、零运维、一个可备份的文件**，加上适合个人 agent 的知识图谱 + 双语 + 自主维护——无需跑向量库、图库或完整 agent 运行时。

如果你的场景是"服务众多用户的产品 / 海量向量 / 长驻自主 agent 运行时" → Mem0/Letta/Zep 是对的。如果是"个人 agent 的记忆，本地、自托管、双语、真图谱召回 + 自维护" → 那是 mnelo 的车道。

---

## 🔄 Repo ↔ live 同步（post-commit hook）

mnelo 有每个 `.py` / `.sql` 文件的两份：仓库（`~/projects/mnelo/`）和 live server 目录（`~/.hermes/memory/`）。仓库自带 **post-commit 钩子**把改动同步到 live、备份旧版、并跑 `health_check.py`：

```bash
cd ~/projects/mnelo && git config core.hooksPath .githooks
```

按设计跳过 `memory.db` / `config.toml` / `*.md` / `tests/`。同步后重启 MCP server：`launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp`。

---

## 🌐 i18n / 国际化

新增一个 locale——改 `i18n_messages.py` 一处，无需改代码。设 `MNELO_MEMORY_LANG=ja` 测试；缺省回落到 `en`，再落到 `msg_id`（可调试，非静默）。

---

## 🚧 已知局限

| 局限 | 缓解 |
|---|---|
| **~50 万向量** @ 512 维单 MacBook（sqlite-vec 暴力） | 启用 **usearch**（HNSW，任意 CPU）或 **zvec** 做真 ANN |
| 单用户（无多租户） | 别把 8086 端口暴露到局域网 |
| 无 PII 自动检测 | 别存密码 / token / 信用卡 |
| bge-small-zh 中文特调 | 英文为主的工作负载换 `bge-small-en-v1.5` |
| L2 自主层**默认关**（显式启用） | 准备好了再开 `[l2] enabled=true`，先 dry-run |

---

## 🧪 跑测试

```bash
cd mnelo
python3 -m pytest tests/ -q
# 738 passed, 1 skipped（~210s）
```

---

## 📜 许可

MIT。见 [`LICENSE`](LICENSE)。

---

## 🙏 致谢

- [sqlite-vec](https://github.com/asg017/sqlite-vec) — 向量扩展
- [fastembed](https://qdrant.github.io/fastembed) — 嵌入器封装
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) — 中文嵌入模型
- [usearch](https://github.com/unum-cloud/usearch) / [zvec](https://github.com/alibaba/zvec) — 可选向量后端
- [MCP](https://modelcontextprotocol.io) — 协议规范
- [Hermes Agent](https://nousresearch.com/hermes) — 首要集成目标

---

> Hermes = 信使之神。
> mnelo = 他的记忆层。
>
> 2026-07-18 由 [chinesewebman](https://github.com/chinesewebman) + [Hermes Agent](https://nousresearch.com/hermes) 构建。
