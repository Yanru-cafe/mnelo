# mnelo

> **mnelo** = μνήμη + λόγος（希腊语：*记忆* + *理性*）。
> 面向 AI Agent 的本地优先、单文件知识图谱记忆层——带**可选自主维护层**和**会话状态摘要**（Agent 开场自动注入）。

| [English](README.md) | 简体中文 |

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-文档)
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
- **标准 MCP，零锁定**——22 个工具，streamable-http（推荐）/ SSE /
  stdio / dual（同端口 SSE + streamable-http）；兼容 Hermes、Claude
  Code、Cursor 或任意 MCP 客户端
- **适合 \$10/年美国 VPS**——向量后端（usearch f16 / zvec INT8）让
  内存 + 磁盘足够小，可跑 KVM1 1 GB / 25 GB SSD；一个盒子 = 完整记
  忆系统 + agent 中转

## 环境要求

- **Python 3.10+** — `usearch>=2.26`（向量搜索后端）只发布 Python 3.10+
  的 wheel。Python 3.9 及更低版本**不受支持**。macOS（arm64/x86_64）、Linux、
  Windows WSL2 均可。
- ~200 MB 磁盘（embedder 模型缓存 `BAAI/bge-small-zh-v1.5`，首次运行下载）
- 可选：`sqlite-vec` 提供 vec0 快速路径——运行时自动检测，不可用时自动
  回落到 `usearch`

## 安装

```bash
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo
bash scripts/install.sh        # 一键：venv + pip + init_db + 服务守护
                               # (macOS launchd / Linux systemd) + 鉴权 token
```

或手动：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/init_db.py
# 启动 server — streamable-http 是推荐 transport
.venv/bin/python mcp_server.py --transport streamable-http \
  --host 127.0.0.1 --port 8086
```

验证：

```bash
python3 scripts/health_check.py
```

非技术用户：把这一句话提示词交给任意 AI 编程 agent（Claude Code、
Hermes、Cursor…），它会一次性装好并接入 mnelo——见
[docs/AGENTS.md](docs/AGENTS.md#one-line-install-prompt)。

## 多 agent 通过 Tailscale 共用

一个 mnelo 实例可以服务**跨多机的多个 AI agent**——你的 MacBook、
$10/年的 VPS、Raspberry Pi、或者朋友的笔记本，只要在同一个
Tailscale mesh 里，所有 agent 共享同一个 `memory.db` 且 id 互不撞。

### mnelo 提供什么

- **`host:` namespace 隔离** — 每个 agent 写入自己前缀下（`host:macbook`、
  `host:vps-agent-1`…），写入永不撞 ID。同一 DB 不同视角，无需全局锁。
- **Tailscale CGNAT host 白名单** — `mcp_server.py` 接受 Tailscale
  `100.x.x.x` IP 作为合法 bind target，mesh peer 可以直接连入，不用
  把服务暴露到公网。
- **`MneloRemoteClient`** — 客户端封装（`api/mnelo_client.py`），锁定
  `source='hermes-gw'`，gateway agent 的写入可被标记 + 查询。
- **`install.sh --listen-mode`** — 安装时两个模式（仅交互式安装；
  非交互式默认 loopback）：
  - `loopback`（默认，单机）— `--host 127.0.0.1`，最安全。如果你在 admin
    console 注册了 `*.ts.net` Service，Tailscale daemon 也会把 Service
    流量 forward 到这里。
  - `Tailscale mesh`（多 agent）— `--host 0.0.0.0`，接受 mesh peer 直接
    IP 连接。host 白名单仍拒绝 LAN / 公网 / 非 CGNAT IP，所以开放度
    等同于你的 Tailscale ACL 策略。
  - 想要 Service vs 裸 IP 路由的精细决策，见
    [docs/AGENTS.md §1.5](docs/AGENTS.md#15-decide-the-listen-mode-single-machine-vs-multi-agent-affects-mcp_server---host)。
- **Per-agent 配置（`config.toml`）** — `[rate_limit]`、`[validation]`、
  `[task]`、`[client]` 4 个 section 按部署可调，每台机器策略不同
  不需要改代码。

### 5 分钟最小部署

**服务端**机器（拥有 `memory.db` 的那台）：

```bash
# 1. 安装（交互式；Tailscale mesh 模式答 "2"）
bash scripts/install.sh

# 2. 查你的 Tailscale IP
tailscale ip -4                  # → 100.x.x.x

# 3. 把 auth token 分享给客户端（在 ~/.config/mnelo/auth_token）
cat ~/.config/mnelo/auth_token
```

每台**客户端**机器（MacBook、VPS、R Pi…）：

```bash
pip install -r requirements.txt

# 4. 指向服务端（它的 Tailscale IP）
export MNELO_MEMORY_URL="http://100.x.x.x:8086/mcp"

# 5. 设 auth token（从服务端 step 3 复制）
export MNELO_AUTH_TOKEN="<paste-from-server-step-3>"

# 6. 验证连接（AGENTS §1.5 还有 tailscale ip -4 curl 测试）
python3 scripts/health_check.py
```

就这些——不需要端口转发、不需要公网证书。Tailscale mesh 负责
传输加密 + ACL；mnelo 负责 auth token + namespace 隔离。

### 参考

- 完整 listen-mode 决策树（什么时候用 `127.0.0.1` vs `0.0.0.0`、
  Tailscale Service vs 裸 IP、已知防火墙坑、R Pi / VPS 客户端
  接入）：见
  [docs/AGENTS.md §1.5](docs/AGENTS.md#15-decide-the-listen-mode-single-machine-vs-multi-agent-affects-mcp_server---host)
- 多 agent 远程 client 封装代码：见
  [`api/mnelo_client.py`](api/mnelo_client.py)
- 廉价 VPS 部署完整 story + auth token：见
  [docs/OPERATIONS.md](docs/OPERATIONS.md#vps-deployment-cheap-us-vps--10year-tier)

## 文档

其余全部内容在 `docs/`：

- [docs/AGENTS.md](docs/AGENTS.md) — 接入 mnelo 作为你的记忆层
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — 安装、服务守护、客户端接入、
  恢复
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 备份 / 恢复、repo ↔ live
  同步、**美国低价 VPS 部署**、已知限制
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
- [docs/SCHEMA.md](docs/SCHEMA.md) — SQLite schema（14 张表）

## 设计原则

1. **本地优先。** 永不调用云 API。Embedder 预下载后离线运行。
2. **单文件。** SQLite。`cp memory.db` 即完整备份。
3. **标准 MCP，零锁定。** 22 个工具，streamable-http / SSE / stdio；
   任意 MCP 客户端可用。
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
# 收集 1,075 个测试；覆盖与延迟数字见
# docs/BENCHMARKS.md → Test coverage
```

## 许可证

MIT。详见 [LICENSE](LICENSE)。

## 致谢

- [usearch](https://github.com/unum-cloud/usearch) /
  [zvec](https://github.com/alibaba/zvec) — 向量后端；默认 `auto`
  链优先 zvec（INT8，需 AVX2+），不可用回落 usearch（f16）
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — legacy：`vec0`
  表保留给 migrate / repair / init_db 工具用；运行时检索后端不再
  写它
- [fastembed](https://qdrant.github.io/fastembed) — 嵌入模型封装
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)
  — 中文嵌入模型
- [MCP](https://modelcontextprotocol.io) — 协议规范

> Hermes = 神的信使。
> mnelo = 他的记忆层。
