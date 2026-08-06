# mnelo

> **mnelo** = μνήμη + λόγος（希腊语：*记忆* + *理性*）。
> 面向 AI Agent 的本地优先、单文件知识图谱记忆层——带**可选自主维护层**和**会话状态摘要**（Agent 开场自动注入）。

| [English](README.md) | 简体中文 |

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-i18n-国际化)
[![Local-first](https://img.shields.io/badge/local--first-100%25-brightgreen)](#-设计原则)
[![Latest release](https://img.shields.io/github/v/release/chinesewebman/mnelo)](https://github.com/chinesewebman/mnelo/releases/latest)

**你的 AI Agent 记忆所栖息的运行时。**

- **永远本地**——一个 SQLite 文件，`cp memory.db` 即完整备份。无
  云、无账号、无订阅。
- **4 路召回 + RRF**——vector / graph / meta / entity 四路融合，无需
  分数归一化（5k 向量时 p50 = **18 ms**）
- **知识图谱原生**——实体 + 类型化关系，每条关系回指源 chunk
- **memory_type 类型谱系 + 零 LLM 分类器**——自动打标每条写入为
  `fact` / `preference` / `episode` / `decision` / `procedure` /
  `ephemeral`；双语（简体/繁體/EN）
- **会话状态摘要**——500–2000 字"现在进展到哪"开场摘要（任意 MCP
  客户端）
- **任务与循环状态机**——有限状态任务、周期循环、停滞任务提议，
  **CAS 保护**转移 + 完整审计链
- **可选自主维护层**——TTL、importance 衰减、事实晋升，含完整
  audit_log + undo。出厂默认关闭。
- **标准 MCP，零锁定**——22 个工具，SSE 协议；兼容 Hermes、Claude
  Code、Cursor 或任意 MCP 客户端

## 安装

```bash
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo
bash scripts/install.sh        # 一键：venv + pip + init_db + plist + 鉴权 token
```

或手动：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/init_db.py
launchctl load ~/Library/LaunchAgents/ai.mnelo.mcp.plist
```

验证：

```bash
python3 scripts/health_check.py
```

非技术用户：把这一句话提示词交给任意 AI 编程 agent（Claude Code、
Hermes、Cursor…），它会一次性装好并接入 mnelo——见
[docs/AGENTS.md](docs/AGENTS.md#one-line-install-prompt)。

## 文档

其余全部内容在 `docs/`：

- [docs/AGENTS.md](docs/AGENTS.md) — 接入 mnelo 作为你的记忆层
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — 安装、launchd、客户端接入、
  恢复
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 备份 / 恢复、repo ↔ live
  同步、launchd 命令、已知限制
- [docs/VECTOR_BACKENDS.md](docs/VECTOR_BACKENDS.md) — usearch (f16) vs
  zvec (INT8 + 原生 FTS) + AVX2 检测 + 崩溃诊断
- [docs/L2_MAINTENANCE.md](docs/L2_MAINTENANCE.md) — 自主维护层细节
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — 延迟 / 内存占用 /
  多语 / 测试覆盖
- [docs/COMPARISON.md](docs/COMPARISON.md) — vs Mem0 / Letta / Zep /
  Cognee
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 模块布局
- [docs/DESIGN.md](docs/DESIGN.md) — 设计蓝图
  （[docs/DESIGN_TASK_LOOP.md](docs/DESIGN_TASK_LOOP.md) 是任务/循环子系统）
- [docs/SCHEMA.md](docs/SCHEMA.md) — 12 张表的 schema
- [docs/REVIEW_LOG.md](docs/REVIEW_LOG.md) — review-pass 审计历史

## 设计原则

1. **本地优先。** 永不调用云 API。Embedder 预下载后离线运行。
2. **单文件。** SQLite。`cp memory.db` 即完整备份。
3. **标准 MCP，零锁定。** 22 个工具，SSE 协议；任意 MCP 客户端可用。
4. **通用优先。** 默认按协议通用（任意 MCP 客户端），客户端特定粘
   合是薄层、有文档的适配器。
5. **内容中立。** mnelo 不评判内容——忠实存储和检索调用方传入的任
   何东西。它守护**机制**（注入、身份、完整性），不守护**内容**。
6. **单一真源。** 派生视图（摘要、规范事实）绝不携带源 chunk 没有
   的信息。
7. **稳定可预期。** 无魔法。fail-fast 优于静默降级。显式 opt-in
   优于意外默认。
8. **可复现。** [docs/BENCHMARKS.md](docs/BENCHMARKS.md) 所有数字
   都可复现。

## 跑测试

```bash
python3 -m pytest tests/ -q
# 738 passed, 1 skipped (~210s)  [2026-08]
```

## 许可证

MIT。详见 [LICENSE](LICENSE)。

## 致谢

- [usearch](https://github.com/unum-cloud/usearch) /
  [zvec](https://github.com/alibaba/zvec) — 向量后端（默认 `auto`
  链）
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — 工具用
  `vec0` 虚表
- [fastembed](https://qdrant.github.io/fastembed) — 嵌入模型封装
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)
  — 中文嵌入模型
- [MCP](https://modelcontextprotocol.io) — 协议规范

> Hermes = 神的信使。
> mnelo = 他的记忆层。
