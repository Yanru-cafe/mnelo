# mnelo

> **mnelo** = μνήμη + λόγος (Greek: *memory* + *reason*).
> Local-first, single-file, knowledge-graph memory layer for AI agents — with an optional **autonomous maintenance layer** and a **session-state digest** injected into your agent at startup.

> **中文用户**: [README.zh.md](README.zh.md) 提供中文版。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.26-green)](https://modelcontextprotocol.io)
[![Bilingual](https://img.shields.io/badge/i18n-EN%20%2B%20中文-blueviolet)](#-i18n)
[![Local-first](https://img.shields.io/badge/local--first-100%25-brightgreen)](#-design-tenets)
[![CI](https://github.com/chinesewebman/mnelo/actions/workflows/ci.yml/badge.svg)](https://github.com/chinesewebman/mnelo/actions/workflows/ci.yml)

A memory layer for AI agents. Remembers across **4 dimensions** — vector semantics, knowledge graph, full-text metadata, and entity identity — so every decision can be traced back to the conditions that produced it. One local SQLite file, shared by every local MCP client. **Zero cloud, zero lock-in.**

## 🧭 What is this? (plain English, for non-technical readers)

mnelo is a **memory for your AI assistant** — like a notebook your AI carries from one conversation to the next.

- **It remembers you** — who you are, your preferences, the important facts.
- **It remembers what's happening** — recent decisions, ongoing work.
- **It files memories automatically** — no manual organizing needed.
- **It keeps itself tidy over time** (optional) — stale stuff fades, expired stuff is cleaned up, and every change can be undone.
- **It greets your AI each session** — a short "where things stand" summary, so your assistant starts every chat already knowing the context.

It runs **entirely on your own computer** (a single local file). Nothing goes to the cloud, no account, no subscription.

**If you're not technical**: you don't need to read the rest of this page. The setup is designed so you can hand this page to any AI coding agent (Claude Code, Cursor, …) and it will install mnelo for you — see [🤖 One-line agent install](#-one-line-agent-install). After that, your AI uses mnelo automatically.

---

**What's inside (2026-08):**
- **4-way recall + RRF** — vector / graph / meta / entity lanes fused without score normalization
- **memory_type taxonomy** — every memory auto-classified (fact / preference / episode / decision / procedure / ephemeral) by a zero-LLM rule classifier, bilingual (简体/繁體/EN)
- **L2 autonomous maintenance layer** — optional hygiene pass (importance decay, per-type TTL, purge candidates) with a full **audit_log + undo** trail; fact-promotion that graduates high-value memories to canonical facts
- **Session-state digest** — a 500–2000 char "current state" summary injected into your agent at session start (Claude Code SessionStart hook / MCP resource)
- **Pluggable vector backends** — `sqlite-vec` default (any CPU), `usearch` (HNSW, any CPU), `zvec` (HNSW + native FTS, AVX2+)

**Why 4-way recall wins**: each lane catches what the others miss — vector misses literal terms (product / category codes), meta misses semantic paraphrases, graph misses orphaned chunks, entity misses long-form prose. Four lanes run in parallel (WAL-mode concurrent reads, p50 = **8.5 ms** / p95 = **10 ms** on baseline 6.3k chunks), and RRF fuses their ranks without any score normalization. See [🔀 What is RRF?](#-what-is-rrf) for the math.

---

## ⚡ At a glance

| | |
|---|---|
| **Storage** | Single SQLite file (~45 MB @ 4498 entities / 4343 chunks, 2026-08 measured) |
| **Vector backends** | `sqlite-vec` (default, any CPU) · `usearch` (HNSW, any CPU) · `zvec` (HNSW+FTS, AVX2+) |
| **Embedder** | `bge-small-zh-v1.5` (512-dim, CN-native; EN/multilingual swappable) |
| **Recall** | 4-way hybrid: `vector + graph + meta + entity` → RRF fusion |
| **Memory types** | fact / preference / episode / decision / procedure / ephemeral (auto-classified, zero-LLM) |
| **Autonomous layer** | optional L2 maintenance: decay / TTL / purge, audit_log + undo, fact-promotion |
| **Session digest** | 500–2000 char summary injected at session start (Claude Code hook / MCP resource) |
| **Protocol** | MCP over SSE (127.0.0.1:8086) — **14 tools**, Bearer auth |
| **Latency (warm)** | p50 = **8.5 ms**, p95 = **10 ms** (baseline 6.3k chunks) |
| **LOC** | ~4000 lines Python (memory.py + classifier + search adapter + scripts + client) |
| **Dependencies** | `mcp[cli]`, `sqlite-vec`, `fastembed` (+ optional `usearch` / `zvec`) |
| **i18n** | English + 中文 first-class; classifier handles 简体/繁體/EN |

---

### 🔀 What is RRF?

**RRF = Reciprocal Rank Fusion** ([Cormack et al., 2009](https://dl.acm.org/doi/10.1145/1571941.1572114)). The simplest recipe that actually beats weighted-score tuning when fusing results from heterogeneous search lanes.

The core idea: each lane ranks results independently; merge by summing `1 / (k + rank_i)` across lanes, `k=60`.

```
Lane A (vector):   doc1=1, doc3=2, doc5=3
Lane B (graph):    doc2=1, doc1=2, doc7=3
Lane C (meta):     doc5=1, doc1=3, doc9=2

Final score = Σ_lanes 1 / (60 + rank_in_lane)
→ doc1: 1/61 + 1/62 + 1/63 = 0.0483   ← wins
```

**Why RRF over weighted-score fusion?**

| | RRF | Weighted score fusion |
|---|---|---|
| Needs score normalization? | **No** — rank-only | Yes (each lane's scale must be calibrated) |
| Robust to one lane going wild? | **Yes** — outlier ranks only contribute `1/(60+rank)` | No — skewed scores can dominate |
| New lane added? | Just add it | Re-tune all weights |
| Implementation cost | ~5 lines | Score calibration + weight grid search |

mnelo uses canonical `k=60` for the 4 lanes, plus a small `0.05/sqrt(rank)` boost when a known **category-code** entity matches (e.g. a product SKU) — configurable, defaults to a curated list of entity kinds.

---

## ✨ Features

### 🧠 Knowledge-graph aware
Every chunk can link to typed entities and the relations graph is queryable. **Entity `kind` is an open taxonomy** — you define your domain's kinds (`product`, `person`, `location`, `category`, `canonical_fact`, …; whatever fits). `memory_graph_query` returns 2-hop neighbors; `memory_reason` (planned) returns full paths.

### 🏷️ memory_type taxonomy + zero-LLM classifier
Every chunk carries a type that governs its lifecycle (§3.0): `fact` / `preference` / `episode` / `decision` / `procedure` / `ephemeral`. A **rule classifier** (P1a, no LLM, deterministic) auto-tags new writes by strong markers — bilingual (简体/繁體 via char-map normalization / EN). Ambiguous input stays `fact` ("宁缺毋滥"). Facts can later be **promoted** to `canonical_fact` entities by the L2 layer.

### 🛡️ L2 autonomous maintenance layer (optional)
When enabled (`[l2] enabled=true`), a `run_maintenance` pass keeps the memory healthy:
- **Importance decay** — stale low-value chunks decay toward a floor (0.1)
- **Per-type TTL** — `ephemeral` 7d / `fact` 365d / `preference` 180d / `episode`+`decision` 730d / `procedure` permanent
- **Purge candidates** — decayed/expired items go to a non-destructive report; physical purge is gated behind `confirm_destructive`
- **Fact promotion** — high-frequency facts graduate to structured `canonical_fact` entities
- **Full audit trail** — every proposal/applied/skipped/reverted step lands in `audit_log` with `before/after` JSON + `revert_sql`; `memory_audit_undo` replays it. **Per-proposal transactions** (a bad proposal never poisons the batch).

Everything is **dry-run by default**, idempotent, and protected-entity aware.

### 🗒️ Session-state digest + reversible compression
A **500–2000 char digest** (identity facts + recent key decisions + in-progress sessions) is auto-maintained and injected into your agent at session start — so it opens with "what's the current state of the world" without a recall. Each digest line carries a **provenance pointer** back to its source chunk, so agents can **expand on demand** (reversible compression — compressed view in context, full detail retrievable). Exposed as:
- **MCP resource** `memory://session/digest` (any MCP client)
- **MCP tool** `memory_get_digest(ref=None)` (ref = expand a line)
- **Claude Code SessionStart hook** — auto-injects on every new session

### 🔌 Standard MCP, no lock-in — 14 tools

| Tool | Purpose |
|---|---|
| `memory_remember` | Write a chunk + entities + relations (auto-classified) |
| `memory_recall` | 4-way recall + RRF (filters: type/source/session/asof) |
| `memory_relate` / `memory_forget` / `memory_update` | CRUD with soft-delete + version chain |
| `memory_graph_query` | 2-hop subgraph navigation |
| `memory_stats` | Stats incl. `hygiene` sub-key |
| `memory_get_digest` | Session digest (ref = expand a line) |
| `memory_maintenance` | Run L2 passes (dry-run default) |
| `memory_audit_list` / `memory_audit_undo` | Audit trail + undo |
| `memory_entity_resolve` / `memory_list_entities` / `memory_search_relations` | Entity management |

### 🌏 Bilingual, every layer
- Locale auto-detect (`MNELO_MEMORY_LANG` > `LC_ALL` > `LANG` > `en`)
- Classifier handles 简体 / 繁體 / English (via char-map normalization)

### 🔎 Pluggable vector backends

| Backend | CPU | Features | When |
|---|---|---|---|
| **sqlite-vec** (default) | any | brute-force KNN, zero deps | everything incl. VPS/old clients |
| **usearch** | any | HNSW (real ANN) | larger scale / faster recall on old CPUs |
| **zvec** | **AVX2+** | HNSW + native FTS (BM25 + jieba 中文) | scale + full-text search |

Auto-detected; `[search] backend` switches; if the configured backend is unavailable, the factory **auto-falls back** along the `zvec → usearch → sqlite_vec` chain via subprocess `import`-check. See the [deployment matrix](#-search-backend-deployment-matrix).

---

## 🛡 Design tenets

1. **Local first.** No cloud API calls, ever. Embedder runs offline after pre-download.
2. **Single file.** SQLite. `cp memory.db` = full backup.
3. **Standard MCP, no lock-in.** 14 tools over SSE; works with any MCP client.
4. **Generic-first.** Features default to protocol-generic (any MCP client); client-specific glue (e.g. the Claude Code hook) is a thin, documented adapter — never a new mechanism.
5. **Amoral by design.** mnelo doesn't judge content (legal/secrecy/offense) — it faithfully stores/retrieves. It guards the *mechanism* (injection, identity, integrity), not the *content*.
6. **Single source of truth.** Derived views (digest, canonical facts) never carry information the source chunks don't have.
7. **Boring & predictable.** No magic. Fail-fast over silent degradation. Explicit opt-in over defaults-that-surprise.
8. **Measured.** All numbers in the Benchmark section are reproducible.

---

## 🚀 Quick start

```bash
# 1. Clone
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo

# 2a. One-shot install (recommended) — venv, pip, init_db, plist, auth token
bash scripts/install.sh

# 2b. Or manual:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Init DB
python3 scripts/init_db.py

# 4. Start MCP server
launchctl load ~/Library/LaunchAgents/ai.mnelo.mcp.plist
# (or: MNELO_MEMORY_SERVER_PORT=8086 python3 mcp_server.py --transport sse)

# 5. Use from Python — writes are auto-classified, digest is available
python3 -c "
import sys; sys.path.insert(0, 'api')
from mnelo_client import MneloClient
c = MneloClient()
c.remember('我偏好简洁日报', source='conversation')       # auto → preference
for h in c.recall('偏好', top_k=3, filters={'type': 'preference'}):
    print(h['method'], h['content'][:40])
print(c.get_digest()['content'][:100])                     # session digest
"
```

### 🤖 One-line agent install

Non-technical? Skip all the steps above — hand this to any AI coding agent (Claude Code, Hermes, Cursor, …). **It installs and adopts in one go:**

> **Install and adopt mnelo from https://github.com/chinesewebman/mnelo as my memory system:**
> 1. Clone, set up the venv, `pip install -r requirements.txt`, run `scripts/init_db.py`, launch the MCP server on port 8086, and verify with `scripts/health_check.py` (report back when `MCP server ready`).
> 2. **Ask me my primary language first** (中文 / English / mixed), then set the matching embedding model in `config.toml [embedder]` *before* first use (switching later requires re-initializing the DB).
> 3. Choose the vector backend for this machine: default `sqlite-vec` unless there's a reason to upgrade; `usearch` for any CPU; **`zvec` only if this CPU supports AVX2+** (never on old CPUs).
> 4. After it's running, **update your own SOUL.md / CLAUDE.md** to declare: "I use the mnelo MCP server (`http://127.0.0.1:8086/sse`) as my memory system — `memory_remember` to persist, `memory_recall` to retrieve, `memory_get_digest` for the session digest."

The agent handles venv creation, pip install, the embedding-model download, and the health probe. Typical install ~90s (the 92 MB model download is the slow part).

### 🤖 Session-state injection — generic, any MCP client

mnelo is a **generic agent memory system** — Hermes, Claude Code, Cursor, or any MCP client use it directly. The digest rides **standard MCP** (initialize + resource), so injecting it at session start needs **no per-client code**:

```toml
# ~/.hermes/memory/config.toml — works for ANY MCP client:
[digest]
inject_on_initialize = true   # digest rides the MCP initialize response
```

- **Hermes / Cursor / any MCP client**: just flip that one config — the digest is delivered via MCP initialize + the `memory://session/digest` resource. No integration code.
- **Claude Code (optional adapter)**: agents that don't read the initialize resource can use the included **SessionStart hook** (`scripts/session_start_digest.py`, wired in `.claude/settings.json`). It is a thin adapter over the same generic mechanism — **fault-tolerant** (mnelo down → silent exit 0), **self-bootstrapping** (re-execs into the repo venv if `python3` resolves elsewhere), and emits a `[mnelo-digest]` fenced block (data, not instruction — DESIGN §12).

Either way, your agent opens each session already knowing the current memory state — no recall needed.

---

## 🤖 For AI agents (adopt mnelo as your memory)

If you're an AI agent (Claude Code, Hermes, Cursor, …) setting up mnelo for your user, follow these operational steps:

**1. Choose the vector backend — based on the actual machine.**
- Default **`sqlite-vec`** works on any CPU — use it unless there's a reason not to.
- Larger scale or faster recall on an older CPU → **`usearch`** (any CPU).
- **`zvec`** adds native full-text, but **requires AVX2+** — do **not** install it on old CPUs (it crashes on `import` there). Detect the CPU or ask.
- Configure via `config.toml [search] backend` or env `MNELO_MEMORY_SEARCH_BACKEND`.

**2. Ask the user about their primary language before picking an embedding model** (switching later requires re-initializing the DB — so ask *before* first use):
- Chinese → `bge-small-zh-v1.5` (default, 512d)
- English → `bge-small-en-v1.5` (384d)
- Mixed / multilingual → `paraphrase-multilingual-MiniLM-L12-v2` (384d)

**3. After install, update your own config to adopt mnelo as your memory system.** Add a note to your `SOUL.md` / `CLAUDE.md` (or equivalent):

> **Memory**: I use the mnelo MCP server (SSE at `http://127.0.0.1:8086/sse`) as my memory system.
> - `memory_remember` — persist important facts & decisions, with a type (`preference`, `decision`, …)
> - `memory_recall` — retrieve before answering anything that might not be in context
> - `memory_get_digest` / `memory://session/digest` — the session-state summary injected at start
> Keep mnelo running. If it's down, work without it and note the memory gap.

**4. Verify** — run `scripts/health_check.py` (or hit `/health`) and confirm the server is ready before declaring done.

---

## 🔎 Search backend (deployment matrix)

mnelo's vector index backend is pluggable ([DESIGN §3.6/§8.3](docs/DESIGN.md)). **Default `sqlite-vec` needs zero extra deps and runs on any CPU.**

| Backend | CPU requirement | Features | When to use |
|---|---|---|---|
| **sqlite-vec** (default) | any (pure SQLite ext) | brute-force KNN; zero deps | all machines, incl. VPS / old clients |
| **usearch** (optional) | any (hardware-agnostic) | **HNSW** real ANN | need real ANN on old CPUs |
| **zvec** (optional) | **AVX2+** (M-series / 2020+ x86_64 / modern ARM) | HNSW + native FTS (BM25 + jieba 中文) | scale + full-text search |

**Deployment rules**:
- **Not installed** → default `sqlite_vec`, fully functional, no config.
- **Installed but CPU unsupported** (e.g. `import zvec` crashes on an old VPS) → mnelo detects in a **subprocess** (safe — doesn't crash the host process) and **auto-falls back** along the chain below.
- **Enable usearch/zvec**: `pip install -r requirements-usearch.txt` (or `-zvec.txt`) + `config.toml` `[search] backend = 'usearch'|'zvec'` (or env `MNELO_MEMORY_SEARCH_BACKEND`).
- `scripts/health_check.py` reports the active backend; `/health` surfaces maintenance recommendations when degraded.

**Auto-fallback chain (8/5 主人 decision)**: `build_search_index()` factory implements a **zvec → usearch → sqlite_vec** cascade via subprocess `import`-check — install one, run; the factory picks the highest-priority backend that imports cleanly. No fail-fast: even on a CPU that can't run zvec, the system stays usable (usearch if installed, else sqlite_vec). The factory always returns a working index.

**Switching backends** (sqlite_vec → usearch/zvec): requires full re-embed of all chunks:
```bash
python scripts/rebuild_index.py --backend usearch    # full re-embed from chunks
python scripts/repair_index.py --backend usearch     # remove orphan vectors (chunk gone but index not)
# Or dry-run first:
python scripts/rebuild_index.py --backend usearch --dry-run
```

> ⚠️ zvec 0.6 on pre-Ivy-Bridge x86_64 crashes on `import` — don't install it there. The factory will skip it automatically, but installing a package you can't run wastes disk.

---

## 📊 Benchmark results

All numbers measured on a single MacBook (M-series). Current baseline (2026-08): `memory.db` = **~44.7 MB + 0.7 MB WAL / 4,498 entities / 4,343 chunks**.

### Latency

| Metric | Value | Notes |
|---|---|---|
| **p50** | **8.5 ms** | warm, 6.3k chunks, 4-way concurrent |
| **p95** | **10 ms** | same |
| **p50 (10k seed)** | **23 ms** | `scripts/benchmark.py --chunks 10000` |
| **p95 (10k seed)** | **29 ms** | vector search scales with chunk count |
| **avg (24h warm)** | 34.4 ms | incl. cold-start outliers |
| **cold start** | ~1.1 s | MCP launch + embedder load |

Reproduce: `python scripts/benchmark.py --chunks 10000 --queries 100 --json bench.json`

### Memory footprint

One MCP server process, idle (macOS M-series): **~270 MB RSS** — of which the embedder (bge-small-zh weights + onnxruntime + tokenizer) is ~200 MB, constant regardless of data size; the rest (~70 MB) is Python + MCP + SQLite + sqlite-vec. The 92 MB model file inflates to ~200 MB resident (float32 load + ONNX arena + tokenizer) — **file size ≠ RAM cost**.

Model lives in the HuggingFace Hub cache (`~/.cache/huggingface/hub/`); auto-downloaded on first use, shareable with other tools.

### 🌐 Multilingual models

Default `bge-small-zh-v1.5` is CN-native; swap via `config.toml` `[embedder]` (or env) for English (`bge-small-en-v1.5`, 384d) or multilingual (`paraphrase-multilingual-MiniLM-L12-v2`, 384d). ⚠️ Switching models requires re-initializing the DB (vector dim is baked into schema).

### Test coverage

```
$ python3 -m pytest tests/ -q
# 738 passed, 1 skipped (~210s)  [2026-08]
```

51 test files covering: core CRUD/recall, memory_type classifier (双语/繁简), L2 hygiene/watermark/atomicity, audit undo, digest, search-index backends, Claude Code hook, schema consistency.

---

## 🏗 Architecture / Project structure

```
mnelo/
├── memory.py            ← core Memory class (~1600 LOC): CRUD + 4-way recall + RRF
│                           + L2 (run_maintenance / audit / digest / promote)
├── classify.py          ← P1a rule classifier: memory_type auto-tag (简体/繁體/EN)
├── search_index.py      ← SearchIndex adapter: sqlite-vec / usearch / zvec backends
├── mcp_server.py        ← MCP server (SSE), 14 tools, Bearer auth, /health
├── config.py            ← env > TOML > defaults (incl. [search], [digest], [l2])
├── schema.sql           ← 12 tables incl. audit_log + memory_type/user_confirmed/processed_at
├── embedder.py          ← fastembed wrapper (bge-small-zh)
├── entity_resolve.py    ← entity merge + alias resolution
├── validation.py        ← input sanitization (size caps, control/bidi chars)
├── auth.py / metrics.py / mnelo_locale.py / i18n_messages.py
│
├── api/mnelo_client.py  ← MneloClient (14 tools, incl. get_digest / maintenance / audit)
│
├── scripts/
│   ├── init_db.py / install.sh / health_check.py
│   ├── session_start_digest.py   ← Claude Code SessionStart hook
│   ├── rebuild_index.py / repair_index.py / repair_vectors.py
│   ├── migrate_to_mnelo.py / import_holdings.py / import_identity_facts.py
│   └── identity_fact_manager.py / benchmark.py
│
├── tests/               ← 51 files (classifier, L2, digest, search-index, hooks, schema)
│
└── docs/
    ├── DESIGN.md            ← v0.13 design blueprint (data model, L2, backends, security)
    ├── TASKS_*.md           ← 6 implementation handbooks (H/E/G/A/S/P series)
    ├── ARCHITECTURE.md / SCHEMA.md / RUNBOOK.md
```

---

## 🆚 Why mnelo? (vs the mainstream agent-memory landscape)

mnelo sits in a different lane from the big agent-memory frameworks. **Mem0 / Letta / Zep / Cognee** are built for *product-scale deployments* — managed services or self-hosted servers, often requiring external vector/graph DBs or a full agent runtime. **mnelo is the local-first, single-file, zero-cloud option**: one SQLite file, standard MCP, works with any agent, bilingual out of the box, with an optional autonomous maintenance layer.

| | **mnelo** | **Mem0** | **Letta (MemGPT)** | **Zep/Graphiti** | **Cognee** |
|---|---|---|---|---|---|
| **Deployment** | one SQLite file | managed service / self-host | agent runtime (heavy) | graph DB + service | self-host pipelines |
| **Cloud required** | **no, ever** | optional | no | optional | no |
| **Zero-dependency install** | **3 pip pkgs** | needs vector DB | ~500MB runtime | needs Neo4j etc. | needs KG stack |
| **Knowledge graph** | ✅ native | ✅ (paid tier) | – | ✅ (core) | ✅ (core) |
| **4-way RRF recall** | ✅ | – | – | – | – |
| **Bilingual (中/EN), 简/繁 classifier** | ✅ | – | – | – | – |
| **Autonomous maintenance** | ✅ (L2, audit/undo) | ✅ (LLM extraction) | ✅ (self-editing) | – | – |
| **Temporal model** | ✅ (`valid_until` + `asof`) | – | – | ✅ (bi-temporal) | – |
| **Session digest injection** | ✅ (any MCP client) | – | ✅ (core memory) | – | – |

**Honest trade-off**: the mainstream frameworks are more mature, have managed cloud tiers, and target large-scale or long-running-agent use cases. mnelo prioritizes the opposite: **simplicity, local-first, zero ops, one backup-able file**, and a knowledge graph + bilingual + autonomous maintenance that fits a personal agent — without needing to run a vector DB, a graph DB, or a full agent runtime.

If your use case is "a product serving many users / massive vector scale / a long-lived autonomous agent runtime" → Mem0/Letta/Zep are the right tools. If it's "a personal agent's memory, local, self-hosted, bilingual, with real knowledge-graph recall and self-maintenance" → that's mnelo's lane.

---

## 🔄 Repo ↔ live sync (post-commit hook)

mnelo has two copies of every `.py` / `.sql` file: the repo (`~/projects/mnelo/`) and the live server dir (`~/.hermes/memory/`). The repo ships a **post-commit hook** that syncs edited files to live, backs up the old version, and runs `health_check.py` after:

```bash
cd ~/projects/mnelo && git config core.hooksPath .githooks
```

Skips `memory.db` / `config.toml` / `*.md` / `tests/` (by design). Restart the MCP server after sync: `launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp`.

---

## 🌐 i18n

Add a new locale — 1 edit in `i18n_messages.py`, no code change. Set `MNELO_MEMORY_LANG=ja` to test; miss falls back to `en`, then to `msg_id` (debuggable, not silent).

---

## 🚧 Known limitations

| Limit | Workaround |
|---|---|
| **~500K vectors** @ 512-dim on a single MacBook (sqlite-vec brute-force) | Enable **usearch** (HNSW, any CPU) or **zvec** for real ANN |
| Single-user (no multi-tenant) | Don't expose port 8086 to LAN |
| No PII auto-detection | Don't store passwords / tokens / credit cards |
| bge-small-zh is CN-tuned | Swap to `bge-small-en-v1.5` for EN-heavy workloads |
| L2 autonomous layer is **off by default** (explicit opt-in) | Enable `[l2] enabled=true` when ready; dry-run first |

---

## 🧪 Run tests

```bash
cd mnelo
python3 -m pytest tests/ -q
# 738 passed, 1 skipped (~210s)
```

---

## 📜 License

MIT. See [`LICENSE`](LICENSE).

---

## 🙏 Acknowledgements

- [sqlite-vec](https://github.com/asg017/sqlite-vec) — vector extension
- [fastembed](https://qdrant.github.io/fastembed) — embedder wrapper
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) — CN embedding model
- [usearch](https://github.com/unum-cloud/usearch) / [zvec](https://github.com/alibaba/zvec) — optional vector backends
- [MCP](https://modelcontextprotocol.io) — protocol spec
- [Hermes Agent](https://nousresearch.com/hermes) — primary integration target

---

> Hermes = the messenger god.
> mnelo = his memory layer.
>
> Built 2026-07-18 by [chinesewebman](https://github.com/chinesewebman) + [Hermes Agent](https://nousresearch.com/hermes).
