# mnelo

> **mnelo** = μνήμη + λόγος (Greek: *memory* + *reason*).
> Local-first, single-file, knowledge-graph memory layer for AI agents —
> with an optional **autonomous maintenance layer** and a
> **session-state digest** injected into your agent at startup.

| English | [简体中文](README.zh.md) |

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-docs)
[![Local-first](https://img.shields.io/badge/local--first-100%25-brightgreen)](#-design-tenets)
[![Latest release](https://img.shields.io/github/v/release/chinesewebman/mnelo)](https://github.com/chinesewebman/mnelo/releases/latest)

**the runtime your AI agent's memory lives on.**

- **always local** — one SQLite file. `cp memory.db` is a full backup.
  Cloud-free, account-free, subscription-free.
- **4-way recall with RRF** — vector / graph / meta / entity lanes fused
  without score normalization (p50 = **18 ms** @ 5k vectors)
- **knowledge graph native** — entities + typed relations, every
  relation points back to its source chunk
- **memory_type taxonomy + zero-LLM classifier** — auto-tags every
  write as `fact` / `preference` / `episode` / `decision` / `procedure`
  / `ephemeral`; bilingual (简体/繁體/EN)
- **session-state digest** — 500–2000 char "where things stand" summary
  injected at session start (any MCP client)
- **task & loop state machine** — finite-state tasks, periodic loops,
  stuck-task proposals with **CAS-protected** transitions and a full
  audit trail
- **optional autonomous maintenance layer** — TTL, importance decay,
  fact-promotion, with full audit_log + undo. Ship-default off.
- **standard MCP, no lock-in** — 22 tools over streamable-http
  (recommended), SSE, stdio, or dual-mode (SSE + streamable-http on one
  port); works with Hermes, Claude Code, Cursor, or any MCP client
- **fits a $10/year US VPS** — vector backends (usearch f16 / zvec
  INT8) keep RAM + disk small enough for KVM1 1 GB / 25 GB SSD; full
  memory system + agent relay in one box

## install

```bash
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo
bash scripts/install.sh        # one-shot: venv, pip, init_db, service
                               # daemon (macOS launchd / Linux systemd),
                               # auth token
```

or manual:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/init_db.py
# start the server — streamable-http is the recommended transport
.venv/bin/python mcp_server.py --transport streamable-http \
  --host 127.0.0.1 --port 8086
```

verify:

```bash
python3 scripts/health_check.py
```

For non-technical users: hand this single prompt to any AI coding agent
(Claude Code, Hermes, Cursor, …) and it installs + adopts mnelo in one
go — see [docs/AGENTS.md](docs/AGENTS.md#one-line-install-prompt).

## docs

Everything else lives in `docs/`:

- [docs/AGENTS.md](docs/AGENTS.md) — adopt mnelo as your memory
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — install, service daemon, client
  connection, recovery
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — backup / restore, repo ↔
  live sync, **cheap US VPS deployment**, known limitations
- [docs/VECTOR_BACKENDS.md](docs/VECTOR_BACKENDS.md) — usearch (f16) vs
  zvec (INT8 + native FTS) + AVX2 detection + crash triage
- [docs/L2_MAINTENANCE.md](docs/L2_MAINTENANCE.md) — autonomous
  maintenance layer detail
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — latency / memory footprint
  / multilingual / test coverage
- [docs/COMPARISON.md](docs/COMPARISON.md) — vs Mem0 / Letta / Zep /
  Cognee
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout
- [docs/DESIGN.md](docs/DESIGN.md) — design blueprint
  ([docs/DESIGN_TASK_LOOP.md](docs/DESIGN_TASK_LOOP.md) for the task/loop subsystem)
- [docs/SCHEMA.md](docs/SCHEMA.md) — SQLite schema (14 tables)

## design tenets

1. **Local first.** No cloud API calls, ever. Embedder runs offline
   after pre-download.
2. **Single file.** SQLite. `cp memory.db` = full backup.
3. **Standard MCP, no lock-in.** 22 tools over streamable-http / SSE /
   stdio; works with any MCP client.
4. **Generic-first.** Features default to protocol-generic (any MCP
   client); client-specific glue is a thin, documented adapter.
5. **Content-neutral by design.** mnelo doesn't judge content — it
   faithfully stores and retrieves whatever the calling agent supplies.
   It guards the *mechanism* (injection, identity, integrity), not the
   *content*.
6. **Single source of truth.** Derived views (digest, canonical facts)
   never carry information the source chunks don't have.
7. **Boring & predictable.** No magic. Fail-fast over silent
   degradation. Explicit opt-in over defaults-that-surprise.
8. **Measured.** All numbers in [docs/BENCHMARKS.md](docs/BENCHMARKS.md)
   are reproducible.

## run tests

```bash
python3 -m pytest tests/ -q
# 1,075 tests collected; coverage & latency numbers in
# docs/BENCHMARKS.md → Test coverage
```

## license

MIT. See [LICENSE](LICENSE).

## acknowledgements

- [usearch](https://github.com/unum-cloud/usearch) /
  [zvec](https://github.com/alibaba/zvec) — vector backends; default
  `auto` chain tries zvec (INT8, needs AVX2+) first, falls back to
  usearch (f16)
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — legacy: `vec0`
  table kept for migrate / repair / init_db tooling; the runtime search
  backend no longer writes it
- [fastembed](https://qdrant.github.io/fastembed) — embedder wrapper
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)
  — CN embedding model
- [MCP](https://modelcontextprotocol.io) — protocol spec

> Hermes = the messenger god.
> mnelo = his memory layer.
