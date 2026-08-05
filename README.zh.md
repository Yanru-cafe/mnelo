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
- **向量后端（必选二选一，8/6）**——运行时必须 `usearch`（HNSW，任意 CPU，f16）或 `zvec`（HNSW + 原生 FTS + INT8，AVX2+）；`sqlite_vec` 已出局

**为什么 4 路召回赢**：每路互补盲区——向量漏字面（商品/品类代码）、meta 漏语义改写、graph 漏无实体链接的孤儿 chunk、entity 漏长文。四路并行（WAL 并发读，p50 = **18 ms** / p95 = **24 ms** @ zvec 0.6 ~5k 向量，8/6 实测），RRF 无需分数归一化融合。见 [🔀 什么是 RRF？](#-什么是-rrf)。

---

## ⚡ 一瞥

| | |
|---|---|
| **存储** | 单 SQLite 文件（~45 MB @ 4498 entities / 4343 chunks，2026-08 实测） |
| **向量后端** | `usearch`（HNSW，任意 CPU，f16）· `zvec`（HNSW+FTS+INT8，AVX2+）——必选二选一（8/6） |
| **嵌入模型** | `bge-small-zh-v1.5`（512 维，中文原生；英文/多语可换） |
| **召回** | 4 路混合：`vector + graph + meta + entity` → RRF 融合 |
| **记忆类型** | fact / preference / episode / decision / procedure / ephemeral（零 LLM 自动分类） |
| **自主层** | 可选 L2 维护：衰减 / TTL / purge + audit_log + undo + 事实晋升 |
| **会话摘要** | 500–2000 字，开场注入（Claude Code 钩子 / MCP resource） |
| **协议** | MCP over SSE（127.0.0.1:8086）——**14 个工具**，Bearer 认证 |
| **延迟（热）** | p50 = **18 ms**，p95 = **24 ms**（zvec 0.6 @ 5k 向量，4 路并发，8/6 实测） |
| **代码量** | ~4000 行 Python（memory.py + 分类器 + 检索适配器 + scripts + client） |
| **依赖** | 3 个核心 pip（`mcp[cli]`、`usearch`、`fastembed`）+ 1 个 legacy pip（`sqlite-vec`，仅 vec0 表）+ 1 个可选 pip（`zvec` AVX2+）；embedding 模型（`BAAI/bge-small-zh-v1.5`，~92 MB）首次使用时由 fastembed 自动下载 |
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
| **zvec** | **AVX2+** | INT8 | HNSW + 原生 FTS（BM25 + jieba 中文）+ INT8 量化 | 新 CPU（auto 链上层） |
| **usearch** | 任意 | **f16** | **HNSW** 真实 ANN | 旧 CPU / 兜底 |
| **usearch** | 任意 | HNSW（真 ANN） | 更大规模 / 旧 CPU 上更快 |
| **zvec** | **AVX2+** | HNSW + 原生 FTS（BM25 + jieba 中文） | 规模 + 全文检索 |

`[search] backend = 'auto'`（默认）→ zvec（CPU 支持时）> usearch；两者都不可用 → RuntimeError（向量库必选）。sqlite_vec 已出局。见 [部署矩阵](#-向量后端部署矩阵)。

---

## 🛡 设计原则

1. **本地优先**。永不调用云 API。嵌入模型预下载后可完全离线。
2. **单文件**。SQLite。`cp memory.db` = 完整备份。
3. **标准 MCP，无锁定**。14 个工具走 SSE，任何 MCP 客户端可用。
4. **通用优先**。功能默认协议通用（任何 MCP 客户端）；客户端专属胶水（如 Claude Code 钩子）是薄的、有文档的适配器——绝不新造机制。
5. **无道德立场（amoral by design）**。mnelo 不评判内容（合法/涉密/冒犯）——只忠实存取。它守护的是**机制**（注入/身份/完整性），不是**内容**。
6. **信息单源**。派生视图（摘要、canonical facts）绝不携带源 chunk 没有的信息。
7. **boring & predictable**。无魔法。向量后端必选二选一（8/6）：zvec/usearch 都不可用时 fail-fast（RuntimeError）；其他场景仍 fail-fast（配置错误、SQL 错、token 过期等）。显式选择优于意外默认。
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
> 3. 向量后端必选二选一：`auto`（默认）→ 本机支持 AVX2+ 用 **`zvec`**（INT8），否则 **`usearch`**（f16）。`config.toml [search] backend = 'auto'|'usearch'|'zvec'`。
> 4. 跑起来后，**修改你自己的 SOUL.md / CLAUDE.md** 声明："我使用 mnelo MCP server（`http://127.0.0.1:8086/sse`）作为我的记忆系统——`memory_remember` 持久化、`memory_recall` 检索、`memory_get_digest` 会话摘要。"

agent 会处理 venv 创建、pip 安装、嵌入模型下载和健康探针。典型安装约 90 秒（92 MB 模型下载是最慢部分）。

### 🤖 会话状态注入——通用，任何 MCP 客户端

mnelo 是**通用 agent 记忆系统**——Hermes、Claude Code、Cursor 或任何 MCP 客户端直接使用。摘要走**标准 MCP**（initialize + resource），会话开场注入**无需任何客户端代码**：

```toml
# 建议安装位置: 任何显眼、贴近工作区的目录 (如 $HOME/mnelo-data)。
# 通过 MNELO_MEMORY_DIR 改路径; 对任何 MCP 客户端都生效：
[digest]
inject_on_initialize = true   # 摘要随 MCP initialize 响应送达
```

- **Hermes / Cursor / 任何 MCP 客户端**：只改这一个配置——摘要经 MCP initialize + `memory://session/digest` resource 送达，零集成代码
- **Claude Code（可选适配器）**：不读 initialize resource 的 agent 可用自带的 **SessionStart 钩子**（`scripts/session_start_digest.py`，接在 `.claude/settings.json`）。它是同一通用机制的薄适配器——**容错**（mnelo 未跑 → 静默 exit 0）、**自举**（`python3` 解析到 venv 外会自动 re-exec）、输出 `[mnelo-digest]` 围栏块（数据而非指令，DESIGN §12）

无论哪种方式，你的 agent 每个会话开场即知当前记忆状态——无需召回。

---

## 🤖 给 AI agent 的操作指令（把 mnelo 设为你的记忆系统）

如果你是 AI agent（Claude Code、Cursor…），在给你的用户安装 mnelo 时，按以下操作步骤：

**1. 选择安装目录（LIVE_ROOT）——给出建议，让用户选用或自定。**
- **推荐** **显眼、贴近工作区** 的目录——如 `$HOME/mnelo-data`、`$HOME/.local/share/mnelo`，或 `<你的工作区>/mnelo-data`。`$HOME` 下的隐藏点目录（如 `~/.hermes/memory`）也能用，但容易在 dotfiles 里被淹没。
- 别放在刚 clone 的仓库里（记忆数据不是源代码）。
- 给出候选让用户选择或自定路径——**首次使用前定好**（DB、embedder 配置、向量索引都在这；之后再搬要 停服→迁移→重启）。
- 用 env `MNELO_MEMORY_DIR` 指定（更细可用 `MNELO_MEMORY_CONFIG` / `MNELO_MEMORY_DB_PATH`）。写进 shell profile（`~/.profile` / `~/.bashrc`）持久化，让脚本和 server 一致。想改 health_check 的报告目录，用 `MNELO_CRON_OUTPUT_DIR`（默认 `$MNELO_MEMORY_DIR/cron/output`，每次运行会重建）。

**2. 按实际机器选择向量后端（8/6 后端 = 必选二选一）。**
- 默认 **`auto` 链**：`zvec`（CPU 支持 AVX2+ 时） → `usearch`（任意 CPU） → `sqlite_vec`（已淘汰，仅 legacy vec0 表）。`build_search_index()` 工厂实现**自动降级**：装一个就跑，CPU 不重要。
- **`zvec`** 跑原生全文（BM25 + jieba 中文）+ INT8 HNSW，但**需要 AVX2+**——旧 CPU **别装**（那里 `import` 即崩）。检测 CPU 或询问用户。
- **`usearch`** 任意 CPU，HNSW + f16 量化，老 CPU 兜底。
- `config.toml` `[search] backend = 'auto'|'zvec'|'usearch'` 或 env `MNELO_MEMORY_SEARCH_BACKEND` 显式指定。
- 配置：`config.toml [search] backend` 或 env `MNELO_MEMORY_SEARCH_BACKEND`。

**3. 选嵌入模型前先问用户主语言**（之后切换需重新初始化 DB——所以*首次使用前*就问）：
- 中文 → `bge-small-zh-v1.5`（默认，512 维）
- 英文 → `bge-small-en-v1.5`（384 维）
- 混合 / 多语 → `paraphrase-multilingual-MiniLM-L12-v2`（384 维）

**4. 装完后，修改你自己的配置，声明 mnelo 是你的记忆系统。** 在你的 `SOUL.md` / `CLAUDE.md`（或等价文件）加一段：

> **记忆**：我使用 mnelo MCP server（SSE at `http://127.0.0.1:8086/sse`）作为我的记忆系统。
> - `memory_remember` — 持久化重要事实与决策，带类型（`preference` / `decision` / …）
> - `memory_recall` — 回答任何可能不在上下文里的问题前先检索
> - `memory_get_digest` / `memory://session/digest` — 开场注入的会话状态摘要
> 保持 mnelo 运行。如果它停了，就无记忆工作并标注记忆缺口。

**5. 验证** —— 跑 `scripts/health_check.py`（或访问 `/health`），确认 server ready 再宣布完成。

**5. 备份 / 恢复 (TASKS_BACKUP_RESTORE)**。**⚠️ 隐私: 若用户选 GitHub 自动 push (选项 3), 目标仓库**必须是 PRIVATE** —— mnelo.db 含个人记忆、决策、偏好、实体关系 (PII 级别), 推 public repo = 数据泄露。启用 `ai.mnelo.backup.plist` + GitHub 路径前必先验证。mnelo 是单 SQLite 文件 — 备份容易，但写中 `cp memory.db` 不安全 (WAL)。用内置工具:

```bash
# 5a. 手动备份 (写到 config [backup] snapshot_dir + sha256 校验)
python scripts/backup_db.py
python scripts/backup_db.py --dry-run   # 只预览

# 5b. 列快照 + 验 sha256
python scripts/restore_db.py --list

# 5c. 校验快照 (dry-run, 不动 live)
python scripts/restore_db.py --latest --dry-run

# 5d. 实际恢复 (隔离当前 db → memory.db.corrupt-<date>, 原子替换)
python scripts/restore_db.py --from 2026-08-05-030000
# 或: python scripts/restore_db.py --latest
```

**agent 的调度职责**: install.sh 步骤 12 询问用户备份位置 (1: 本地默认, 2: NAS via dr-backup.sh, 3: GitHub repo via dr-backup.sh, 4: 自定义) 和保留份数 (默认 30 ≈ 4 周)。`ai.mnelo.backup.plist` 之后通过 launchd 在周三+周日 03:00 跑。如果用户已配 dr-backup.sh，快照自动 rsync → NAS → GitHub 推送。

**演练 (每月跑)**: `scripts/restore_db.py --latest --dry-run` 验证最新快照是否健康。失败 → 该快照损坏，DESIGN §3.11.2 顺延到上一份。全部失败 → 备份链不可信，查 `logs/mnelo.backup.error.log` 手动跑 `backup_db.py` 复测。

**📌 新增实体 kind**（开放分类——无需注册）：实体 `kind` 是自由文本；"加一个 kind" 本质上就是**开始用它**。用户引入新 kind 时，把它记为约定并一致使用：

> 新增一个实体 kind：`product`，用于记录产品/商品相关的实体。用 `memory_remember` 记录产品时，entities 里带 `kind: 'product'`，并保持命名与别名一致（如 id `product:sku-1024`）。把这个约定记到你的 CLAUDE.md/SOUL.md 并长期一致使用；产品召回时用 `kind: 'product'` 过滤。

可选：把这个 kind 加进 `[recall] boost_kinds` 让它像 `stock` 一样召回浮顶；用 `correct()` 或脚本回填已有实体。

**🎯 根据用户画像，建议新增的 kind**——种子 kind（`stock` / `person` / `concept` …）只是起点，不是上限。第一次接触用户时，扫一眼 TA 的领域（文档、文件、现有数据），按画像提一小套会用到的 kind，再写进 CLAUDE.md/SOUL.md 当约定。以 A 股投资者画像（跟踪持仓、每天看仓位总结日报、会做采购决策）为例：

> `portfolio` — 持仓组合（锚点：id `portfolio:a-share-2026`）
> `position` — 单只持仓（id `position:sh600519`）
> `stock` — 标的本身（种子 kind；用 `position` → `stock` 关联）
> `plan` — 采购 / 下一步计划（"下月采购 CAT-1024"）
> `strategy` — 投资 / 交易策略
> `report` — 周期性报告（每日/每周仓位总结）
> `watchlist` — 自选池

一次提 **5–7 个**即可，别贪多——每个都必须"被跨 chunk 引用"才有存在价值；用户真正引入新概念时再加。

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

## 🔎 向量后端

mnelo 有 2 个支持的 runtime 向量后端（[DESIGN §3.6/§8.3](docs/DESIGN.md)）：

| 后端 | 量化 | 何时用 |
|---|---|---|
| **zvec** | INT8 | 新 CPU（M 系列 / AVX2+）；M2 实测 5k vectors p50=18ms |
| **usearch** | f16 | 任意 CPU——zvec 装不上时的兜底 |

`build_search_index()` 工厂**主进程 import 检测**（macOS 26 launchd fork 子进程跑 zvec native mmap 必现 BlockingIOError，所以走主进程）：装 zvec 就走 zvec，否则走 usearch。两者都不可用 → `RuntimeError`（向量库是必选依赖）。**默认 `auto` 链，`config.toml [search] backend = 'auto'`；显式可用 env `MNELO_MEMORY_SEARCH_BACKEND=zvec|usearch`。**

`scripts/health_check.py` 报告实际生效后端；`/health` 降级时给出维护建议。

**切换后端**（zvec ↔ usearch；不同后端索引格式不兼容）：必须从 chunks 全量重嵌。

```bash
python scripts/rebuild_index.py --backend zvec      # 全量重建
python scripts/repair_index.py --backend zvec       # 清孤儿向量（chunk 删了但索引残留）
# 或先 dry-run:
python scripts/rebuild_index.py --backend zvec --dry-run
```

`sqlite-vec` 仍装在 `requirements.txt` 里——但只给 `migrate` / `repair` / `init_db` 工具留 vec0 虚拟表用，不再做 runtime 后端，不进工厂兜底链。

---

## 📊 测评结果

全部在单台 MacBook（M 系列）实测。当前基线（2026-08）：`memory.db` = **~44.7 MB + 0.7 MB WAL / 4498 entities / 4343 chunks**。

### 延迟

| 指标 | 值 | 说明 |
|---|---|---|
| **p50** | **18 ms** | 热路径，zvec 0.6 @ 5k 向量，4 路并发（8/6 实测） |
| **p95** | **24 ms** | 同上 |
| **p99** | **25 ms** | 同上 |
| **p50（15k 向量）** | **73 ms** | `scripts/benchmark.py --chunks 10000`（重建后 15422 向量） |
| **p95（15k 向量）** | **95 ms** | HNSW 随 chunk 数亚线性增长 |
| **p99（15k 向量）** | **161 ms** | 观察到的最坏情况 |
| **avg（24h 热）** | 10.4 ms | `recall_log` 8/6，232 次，含冷启动离群 |
| **冷启动** | ~1.1 s | MCP 启动 + 嵌入模型加载 |

复现：`python scripts/benchmark.py --chunks 10000 --queries 100 --json bench.json`

### 内存占用

单 MCP server 进程，空闲（macOS M 系列）：**~270 MB RSS**——其中嵌入器（bge-small-zh 权重 + onnxruntime + tokenizer）约 200 MB，**与数据量无关**；其余（~70 MB）是 Python + MCP + SQLite + zvec（或 usearch）。92 MB 模型文件运行时膨胀到 ~200 MB（float32 加载 + ONNX arena + tokenizer）——**文件大小 ≠ 内存成本**。zvec collection 本身约 30 MB on disk（5422 个 512 维向量）。

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

## 🛡️ L2 自主维护层

一个**可选**的层，让长期运行的记忆保持健康，**全程无 LLM**。状态存在 `meta` + `audit_log` 表里；CLI、MCP server、cron 都跑同一份代码。

### 4 个 pass

| Pass | 作用 | 副作用 |
|---|---|---|
| `hygiene` | `importance` 朝 floor (0.1) 衰减 + 按 `memory_type` 的 TTL（ephemeral 7 天 / fact 365 天 / preference 180 天 / episode+decision 730 天 / procedure 永久） | 默认软改，不破坏 |
| `promote` | 高频 `fact` 升级为结构化 `canonical_fact` 实体 | 只追加（新建实体） |
| `decay` | 内置在 `hygiene` 里——陈旧低 importance 降权，**绝不硬删** | 只改元数据 |
| `audit_log_gc` | 修剪 1 年以上的 `audit_log` 条目（TASKS §3 L2 hygiene GC） | 只改元数据 |

### 启用：默认关闭，翻一行 SQL

```sql
-- 默认不启用（DESIGN §5.7 opt-in 设计）：
UPDATE meta SET value='1' WHERE key='l2.enabled';
-- 可选：关掉 dry-run 安全网
UPDATE meta SET value='0' WHERE key='l2.dry_run';

-- 查状态：
SELECT key, value FROM meta WHERE key LIKE 'l2.%';
```

或调 `memory_maintenance` MCP 工具——**默认 dry_run**（跟 meta 设置无关），cron 触发稳。

### 安全保证

- **`dry_run=true` 是默认值**。`hygiene` / `promote` 只产 proposal 不动 chunk；只有 `purge`（TTL 真删）需要显式 `confirm_destructive=true`。
- **每提案独立事务**——坏提案不会毒害整批。
- **每个动作落 `audit_log`**（`before/after` JSON + `revert_sql`）。用 `memory_audit_undo` 回滚。
- **防重入**——`meta.l2.running=1` 期间阻断新一轮。

### 实战数据

一个线上实例（8/6）：累计 `audit_log` ~47 k 行，跨越多轮 L2 sweep。最近一次 `last_run_hygiene` 在快照前 1 小时，`audit_log_total=47408`。同实例 `hygiene` 跑完剩 `decay_floor_chunks=2259`（保存在 0.1）、`purge_backlog=2170`（等 destructive sweep）。

用 `scripts/health_check.py`（或 `/health` 端点）看最新 `last_run_*`。`memory_stats` 也暴露同样指标。

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
| **安装体积** | 3 个核心 pip + 1 个可选 + 92 MB embedding 模型（fastembed 首次自动下） | 需向量库 | ~500MB 运行时 | 需 Neo4j 等 | 需 KG 栈 |
| **知识图谱** | ✅ 原生 | ✅（付费档） | – | ✅（核心） | ✅（核心） |
| **4 路 RRF 召回** | ✅ | – | – | – | – |
| **双语（中/EN），简/繁分类器** | ✅ | – | – | – | – |
| **自主维护** | ✅（L2，审计/撤销） | ✅（LLM 抽取） | ✅（自我编辑） | – | – |
| **时态模型** | ✅（`valid_until` + `asof`） | – | – | ✅（双时态） | – |
| **会话摘要注入** | ✅（任何 MCP 客户端） | – | ✅（核心记忆） | – | – |

**诚实的取舍**：主流框架更成熟、有托管云档、面向大规模或长驻 agent。mnelo 追求相反方向：**简单、本地优先、零运维、一个可备份的文件**，加上适合个人 agent 的知识图谱 + 双语 + 自主维护——无需跑向量库、图库或完整 agent 运行时。

如果你的场景是"服务众多用户的产品 / 海量向量 / 长驻自主 agent 运行时" → Mem0/Letta/Zep 是对的。如果是"个人 agent 的记忆，本地、自托管、双语、真图谱召回 + 自维护" → 那是 mnelo 的车道。

### 安装体积拆解

| 组件 | 大小 | 必装？ | 备注 |
|---|---|---|---|
| **核心 pip 包** | ~3 MB | 必装 | `mcp[cli]`（MCP server + SSE 传输）、`usearch`（向量库兜底）、`fastembed`（embedding 加载） |
| **Legacy pip**（`sqlite-vec`） | ~1 MB | 仅工具需要 | `vec0` 虚拟表给 `migrate` / `repair` / `init_db` 脚本用——不参与运行时检索 |
| **可选 pip**（`zvec`） | ~60 MB native | 仅 AVX2+ 机器 | HNSW + 原生 FTS + INT8；装了就跑，没装自动回退 usearch |
| **Embedding 模型** | ~92 MB | 首次运行必装 | `BAAI/bge-small-zh-v1.5`（中文特调）——由 fastembed 自动下载到 `~/.cache/huggingface/hub/`。可换 `bge-small-en-v1.5`（英文）或 `paraphrase-multilingual-MiniLM-L12-v2`（多语）通过 `config.toml [embedder]` |
| **Python + MCP 运行时** | ~50 MB | 必装 | Python 3.11 + MCP SDK + onnxruntime |
| **向量索引** | 随数据增长 | 必装 | `usearch` 单文件；`zvec` 目录（5422 条 512 维向量约 30 MB） |
| **SQLite DB** | 随数据增长 | 必装 | 一个文件（`memory.db`）；4500 chunks 约 45 MB |

全新安装总磁盘：**~200 MB**（模型占大头）。之后增长完全跟数据量走。无外部服务、无守护进程、无云账号。

---

## 🔄 Repo ↔ live 同步（post-commit hook）

mnelo 有每个 `.py` / `.sql` 文件的两份：仓库（clone 到的任意目录）和 live server 目录（通过 `MNELO_MEMORY_DIR` 设置；默认 `~/mnelo-data`）。仓库自带 **post-commit 钩子**把改动同步到 live、备份旧版、并跑 `health_check.py`：

```bash
cd <你的 clone 目录> && git config core.hooksPath .githooks
```

按设计跳过 `memory.db` / `config.toml` / `*.md` / `tests/`。同步后重启 MCP server：`launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp`。

---

## 🌐 i18n / 国际化

新增一个 locale——改 `i18n_messages.py` 一处，无需改代码。设 `MNELO_MEMORY_LANG=ja` 测试；缺省回落到 `en`，再落到 `msg_id`（可调试，非静默）。

---

## 🚧 已知局限

| 局限 | 缓解 |
|---|---|
| 单用户（无多租户） | 别把 8086 端口暴露到局域网 |
| 无 PII 自动检测 | 别存密码 / token / 信用卡 |
| bge-small-zh 中文特调 | 英文为主的工作负载换 `bge-small-en-v1.5` |
| L2 自主维护层**默认关**（opt-in） | DESIGN §5.7 设计如此——默认关闭；`UPDATE meta SET value='1' WHERE key='l2.enabled'` 一行启用 |

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

- [usearch](https://github.com/unum-cloud/usearch) / [zvec](https://github.com/alibaba/zvec) — 向量后端（8/6 默认：`auto` 链 zvec→usearch）
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — legacy vec0 表，仅迁移/修复工具用，不再做 runtime 后端
- [fastembed](https://qdrant.github.io/fastembed) — 嵌入器封装
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) — 中文嵌入模型
- [MCP](https://modelcontextprotocol.io) — 协议规范
- [Hermes Agent](https://nousresearch.com/hermes) — 主要集成目标

---

> Hermes = 信使之神。
> mnelo = 他的记忆层。
>
> 2026-07-18 由 [chinesewebman](https://github.com/chinesewebman) + [Hermes Agent](https://nousresearch.com/hermes) 构建。
