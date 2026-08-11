# mnelo

> **mnelo** = μνήμη + λόγος (Greek: *memory* + *reason*).
> **Local-first knowledge-graph memory layer for AI agents** — what Mem0
> charges for, in one SQLite file: 4-way RRF + L2 maintenance + bilingual
> classifier. **usearch f16 runs it on a $10/year VPS.**

| English | [简体中文](README.zh.md) |

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-docs)
[![Local-first](https://img.shields.io/badge/local--first-100%25-brightgreen)](#-design-tenets)
[![Latest release](https://img.shields.io/github/v/release/cure4u/mnelo)](https://github.com/cure4u/mnelo/releases/latest)

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

## requirements

- **Python 3.10+** — `usearch>=2.26` (vector search backend) only ships
  wheels for Python 3.10 and newer. Python 3.9 and earlier are not
  supported. macOS (arm64/x86_64), Linux, Windows WSL2 all OK.
- ~200 MB disk for the embedder model cache (`BAAI/bge-small-zh-v1.5`,
  fetched on first run)
- Optional: `sqlite-vec` for vec0 fast path — auto-detected at runtime,
  falls back to `usearch` when unavailable

## install

```bash
git clone https://github.com/cure4u/mnelo.git
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

## multi-agent via Tailscale

A single mnelo instance can serve **multiple AI agents across machines** —
your MacBook, a $10/year VPS, a Raspberry Pi, or a friend's laptop on the
same Tailscale mesh — all writing into one shared `memory.db` without id
collisions.

### What mnelo provides

- **`host:` namespace guard** — every agent writes under its own prefix
  (`host:macbook`, `host:vps-agent-1`, …) so writes never collide. Same
  DB, different views, no global locks.
- **Tailscale CGNAT host whitelist** — `mcp_server.py` accepts Tailscale
  `100.x.x.x` IPs as legitimate bind targets, so mesh peers can dial in
  without exposing the service to the public internet.
- **`MneloRemoteClient`** — a drop-in client wrapper (`api/mnelo_client.py`)
  that locks `source='hermes-gw'` so the gateway agent's writes are
  tagged and queryable.
- **`install.sh --listen-mode`** — two modes at install time (interactive
  install only; non-interactive defaults to loopback):
  - `loopback` (default, single-machine) — `--host 127.0.0.1`, safest.
    Tailscale daemon forwards Service traffic here too if you have a
    `*.ts.net` Service registered in admin console.
  - `Tailscale mesh` (multi-agent) — `--host 0.0.0.0`, accept direct
    mesh-peer IP connections. The host whitelist still rejects LAN /
    public / non-CGNAT IPs, so this is only as open as your Tailscale
    ACL policy.
  - For finer-grained Service-vs-bare-IP routing decisions, see
    [docs/AGENTS.md §1.5](docs/AGENTS.md#15-decide-the-listen-mode-single-machine-vs-multi-agent--affects-mcp_server---host).
- **Per-agent config (`config.toml`)** — `[rate_limit]`, `[validation]`,
  `[task]`, `[client]` sections are per-deployment tunable, so each
  machine's policy can differ without code edits.

### Minimal setup (5 minutes)

On the **server** machine (the one that owns `memory.db`):

```bash
# 1. install (interactive; answer "2" for Tailscale mesh mode)
bash scripts/install.sh

# 2. find your Tailscale IP
tailscale ip -4                  # → 100.x.x.x

# 3. share auth token with client machines (it's at ~/.config/mnelo/auth_token)
cat ~/.config/mnelo/auth_token
```

On each **client** machine (MacBook, VPS, R Pi, …):

```bash
pip install -r requirements.txt

# 4. point at the server (its Tailscale IP)
export MNELO_MEMORY_URL="http://100.x.x.x:8086/mcp"

# 5. set the auth token (from step 3)
export MNELO_AUTH_TOKEN="<paste-from-server-step-3>"

# 6. verify connection (also tailscale ip -4 curl test, see AGENTS §1.5)
python3 scripts/health_check.py
```

That's it — no port forwarding, no public certificates. Tailscale mesh
handles transport encryption and ACLs; mnelo handles auth token +
namespace isolation.

### Reference

- Full listen-mode decision tree (when to use `127.0.0.1` vs `0.0.0.0`,
  Tailscale Service vs bare IP, known firewall gotchas, R Pi / VPS
  client setup): see
  [docs/AGENTS.md §1.5](docs/AGENTS.md#15-decide-the-listen-mode-single-machine-vs-multi-agent-affects-mcp_server---host)
- Multi-agent remote client wrapper code: see
  [`api/mnelo_client.py`](api/mnelo_client.py)
- Cheap VPS deployment story + auth token: see
  [docs/OPERATIONS.md](docs/OPERATIONS.md#vps-deployment-cheap-us-vps--10year-tier)

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
