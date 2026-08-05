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
- **Vector backends (mandatory 二选一, 8/6)** — runtime must be `usearch` (HNSW, any CPU, f16) or `zvec` (HNSW + native FTS + INT8, AVX2+); `sqlite_vec` removed as a backend

**Why 4-way recall wins**: each lane catches what the others miss — vector misses literal terms (product / category codes), meta misses semantic paraphrases, graph misses orphaned chunks, entity misses long-form prose. Four lanes run in parallel (WAL-mode concurrent reads, p50 = **18 ms** / p95 = **24 ms** on zvec 0.6 @ ~5k vectors, 8/6 measured), and RRF fuses their ranks without any score normalization. See [🔀 What is RRF?](#-what-is-rrf) for the math.

---

## ⚡ At a glance

| | |
|---|---|
| **Storage** | Single SQLite file (~45 MB @ 4498 entities / 4343 chunks, 2026-08 measured) |
| **Vector backends** | `usearch` (HNSW, any CPU, f16) · `zvec` (HNSW+FTS+INT8, AVX2+) — mandatory 二选一 (8/6) |
| **Embedder** | `bge-small-zh-v1.5` (512-dim, CN-native; EN/multilingual swappable) |
| **Recall** | 4-way hybrid: `vector + graph + meta + entity` → RRF fusion |
| **Memory types** | fact / preference / episode / decision / procedure / ephemeral (auto-classified, zero-LLM) |
| **Autonomous layer** | optional L2 maintenance: decay / TTL / purge, audit_log + undo, fact-promotion |
| **Session digest** | 500–2000 char summary injected at session start (Claude Code hook / MCP resource) |
| **Protocol** | MCP over SSE (127.0.0.1:8086) — **14 tools**, Bearer auth |
| **Latency (warm)** | p50 = **18 ms**, p95 = **24 ms** (zvec 0.6 @ 5k vectors, 4-way concurrent, 8/6) |
| **LOC** | ~4000 lines Python (memory.py + classifier + search adapter + scripts + client) |
| **Dependencies** | 3 core pip (`mcp[cli]`, `usearch`, `fastembed`) + 1 optional (`zvec` on AVX2+); embedding model (`BAAI/bge-small-zh-v1.5`, ~92 MB) auto-downloaded via fastembed on first use. `sqlite-vec` legacy only. |
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
Every chunk can link to typed entities and the relations graph is queryable. **Entity `kind` is an open taxonomy** — the schema imposes no enum constraint. mnelo ships with a small **seed set** (`stock`, `concept`, `person`, `user`, `canonical_fact`, `identity_fact`); you add your domain's kinds freely (any string works). (`container` for explicit loci is DESIGN-planned, not yet shipped.) `memory_graph_query` returns 2-hop neighbors; `memory_reason` (planned) returns full paths.

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

### 🔎 Vector backends (mandatory 二选一, 8/6)

Vector library is **mandatory**: runtime backend must be `usearch` or `zvec` — `sqlite_vec` is removed as a backend (its `vectors` table stays only as legacy for migration tools).

| Backend | CPU | Precision | Features | When |
|---|---|---|---|---|
| **zvec** | **AVX2+** | INT8 | HNSW + native FTS (BM25 + jieba 中文) | new CPUs (auto chain top) |
| **usearch** | any | **f16** | HNSW (real ANN) | old CPUs / fallback |

`[search] backend = 'auto'` (default) picks zvec → usearch; precision is baked in per backend. See the [deployment matrix](#-search-backend-deployment-matrix).

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
> 3. Vector backend is mandatory 二选一: `auto` (default) picks **`zvec`** if this CPU supports AVX2+ else **`usearch`** (f16). Configure via `config.toml [search] backend = 'auto'|'usearch'|'zvec'`.
> 4. After it's running, **update your own SOUL.md / CLAUDE.md** to declare: "I use the mnelo MCP server (`http://127.0.0.1:8086/sse`) as my memory system — `memory_remember` to persist, `memory_recall` to retrieve, `memory_get_digest` for the session digest."

The agent handles venv creation, pip install, the embedding-model download, and the health probe. Typical install ~90s (the 92 MB model download is the slow part).

### 🤖 Session-state injection — generic, any MCP client

mnelo is a **generic agent memory system** — Hermes, Claude Code, Cursor, or any MCP client use it directly. The digest rides **standard MCP** (initialize + resource), so injecting it at session start needs **no per-client code**:

```toml
# 默认路径: 假如本机是 hermes agent, 装在 ~/.hermes/memory/config.toml — works for ANY MCP client:
# 其他 agent (Claude Code/Cursor/...) 用 $HOME/mnelo-data/config.toml 或自定 path.
[digest]
inject_on_initialize = true   # digest rides the MCP initialize response
```

- **Hermes / Cursor / any MCP client**: just flip that one config — the digest is delivered via MCP initialize + the `memory://session/digest` resource. No integration code.
- **Claude Code (optional adapter)**: agents that don't read the initialize resource can use the included **SessionStart hook** (`scripts/session_start_digest.py`, wired in `.claude/settings.json`). It is a thin adapter over the same generic mechanism — **fault-tolerant** (mnelo down → silent exit 0), **self-bootstrapping** (re-execs into the repo venv if `python3` resolves elsewhere), and emits a `[mnelo-digest]` fenced block (data, not instruction — DESIGN §12).

Either way, your agent opens each session already knowing the current memory state — no recall needed.

---

## 🤖 For AI agents (adopt mnelo as your memory)

If you're an AI agent (Claude Code, Hermes, Cursor, …) setting up mnelo for your user, follow these operational steps:

**1. Choose the install directory (LIVE_ROOT) — propose options, let the user pick or customize.**
- If you're running as **hermes agent** (default), the conventional path is `~/.hermes/memory` (hidden dot-dir in `$HOME`). It works, but hidden dirs are easy to lose track of and get mixed in with unrelated dotfiles.
- For **other agents** (Claude Code, Cursor, …) the **recommended** path is a **visible, workspace-adjacent directory** — e.g. `$HOME/mnelo-data`, `$HOME/.local/share/mnelo`, or `<your workspace>/mnelo-data`. Keep it out of the repo you just cloned — memory data isn't source code.
- Offer candidates and let the user choose or supply their own path — **decide before first use** (the DB, embedder config, and vector index all live here; moving later means stop → migrate → restart).
- Set via env `MNELO_MEMORY_DIR` (finer control: `MNELO_MEMORY_CONFIG` / `MNELO_MEMORY_DB_PATH`). Persist it in the shell profile (`~/.profile` / `~/.bashrc`) so scripts and the server agree. To leave `.hermes` behind entirely, also redirect the health-check report dir with `MNELO_CRON_OUTPUT_DIR` (hermes-agent default `~/.hermes/cron/output`, recreated by `health_check.py` on every run; other agents pick a different one).

**2. Choose the vector backend — based on the actual machine.**
- **`usearch`** (f16) works on any CPU — the default fallback.
- **`zvec`** (INT8) only if this CPU supports AVX2+ — auto chain top.
- **`zvec`** adds native full-text, but **requires AVX2+** — do **not** install it on old CPUs (it crashes on `import` there). Detect the CPU or ask.
- Configure via `config.toml [search] backend` or env `MNELO_MEMORY_SEARCH_BACKEND`.

**3. Ask the user about their primary language before picking an embedding model** (switching later requires re-initializing the DB — so ask *before* first use):
- Chinese → `bge-small-zh-v1.5` (default, 512d)
- English → `bge-small-en-v1.5` (384d)
- Mixed / multilingual → `paraphrase-multilingual-MiniLM-L12-v2` (384d)

**4. After install, update your own config to adopt mnelo as your memory system.** Add a note to your `SOUL.md` / `CLAUDE.md` (or equivalent):

> **Memory**: I use the mnelo MCP server (SSE at `http://127.0.0.1:8086/sse`) as my memory system.
> - `memory_remember` — persist important facts & decisions, with a type (`preference`, `decision`, …)
> - `memory_recall` — retrieve before answering anything that might not be in context
> - `memory_get_digest` / `memory://session/digest` — the session-state summary injected at start
> Keep mnelo running. If it's down, work without it and note the memory gap.

**5. Verify** — run `scripts/health_check.py` (or hit `/health`) and confirm the server is ready before declaring done.

**5. Backup / restore (TASKS_BACKUP_RESTORE).** **⚠️ PRIVACY: if the user picks GitHub auto-push (option 3), the destination repo MUST be private.** mnelo.db contains personal memory, decisions, preferences, entity relationships — PII-level. Pushing to a public repo = data leak. Verify before enabling `ai.mnelo.backup.plist` with a GitHub-backed `snapshot_dir`. mnelo is one SQLite file — easy to back up, but `cp memory.db` is unsafe mid-write (WAL). Use the built-in tools:

```bash
# 5a. Manual backup (writes to config [backup] snapshot_dir + sha256 sidebar)
python scripts/backup_db.py
python scripts/backup_db.py --dry-run   # preview only

# 5b. List snapshots + verify sha256
python scripts/restore_db.py --list

# 5c. Verify a snapshot (dry-run, never touches live)
python scripts/restore_db.py --latest --dry-run

# 5d. Actual restore (isolates current db → memory.db.corrupt-<date>, atomic replace)
python scripts/restore_db.py --from 2026-08-05-030000
# or: python scripts/restore_db.py --latest
```

**Schedule role for the agent**: install.sh step 12 asks the user where to store backups (1: local default, 2: NAS via dr-backup.sh, 3: GitHub repo via dr-backup.sh, 4: custom) and how many to keep (default 30 ≈ 4 weeks). The ai.mnelo.backup.plist then runs `wed+sun 03:00` via launchd. If the user has `dr-backup.sh` already wired, the snapshots end up rsync'd → NAS → GitHub automatically.

**Recovery drill (run monthly)**: `scripts/restore_db.py --latest --dry-run` confirms the most recent snapshot is healthy. If this fails, the snapshot is corrupt — DESIGN §3.11.2 says fall back to the previous one. If all snapshots fail, the backup chain is untrustworthy; investigate `logs/mnelo.backup.error.log` and re-test by manually running `backup_db.py`.

**📌 Adding a new entity kind** (open taxonomy — no registration needed): entity `kind` is free-form; "adding a kind" simply means *starting to use it*. When the user introduces a new kind, record it as a convention and use it consistently:

> Add a new entity kind: `product`, for product-related entities. When using `memory_remember` for products, pass `kind: 'product'` and keep naming/aliases consistent (e.g. id `product:sku-1024`). Record this convention in your CLAUDE.md/SOUL.md and use it consistently; filter product recalls with `kind: 'product'`.

Optionally: add the kind to `[recall] boost_kinds` to give it the same recall boost as `stock`; backfill existing entities via `correct()` or a script.

**🎯 Suggesting new kinds from the user's profile** — the seed kinds (`stock`, `person`, `concept`, …) are a starting point, not a limit. When you first meet a user, skim their domain (documents, files, existing data) and propose a small kind set they'll actually use — then record it in CLAUDE.md/SOUL.md as the convention. For example, for a Chinese A-share investor who tracks holdings and reads a daily position-summary report:

> `portfolio` — the overall holdings set (anchor: id `portfolio:a-share-2026`)
> `position` — a single holding (id `position:sh600519`)
> `stock` — the security itself (seed kind; relate `position` → `stock`)
> `plan` — a purchase / next-step plan ("下月采购 CAT-1024")
> `strategy` — an investment / trading strategy
> `report` — recurring reports (daily/weekly position summaries)
> `watchlist` — a watchlist of candidates

Keep it to **5–7 kinds** — each must earn its place by being referenced across chunks. Add a new one only when the user actually introduces that concept.

**🧠 Using mnelo — write & retrieve well** (what to remember, how to structure it):

**1. `memory_type` — the chunk's lifecycle type.** The rule classifier auto-tags new writes, but pass the type explicitly when you know it:

| Type | Use when | Example you'd classify |
|---|---|---|
| `preference` | a like / dislike / style preference | "我偏好简洁日报" |
| `decision` | a decision + (ideally) its reasoning | "我决定下月采购 CAT-1024" |
| `episode` | a dated event | "今天建仓了 CAT-1024" |
| `procedure` | steps / how-to / workflow | "做周报的流程…" |
| `ephemeral` | draft / placeholder / WIP | "临时草稿，稍后处理" |
| `fact` | everything else (default) | — |

Write: `memory_remember(content, ..., memory_type='decision')` when you know it; **omit it to let the auto-classifier decide** (it handles 简体/繁體/EN).

**2. Entity `kind` — how to structure concepts.** Create an entity when a thing is **referenced across chunks, has aliases, or is a graph anchor** — not for one-off mentions. Keep IDs consistent: `kind:slug` (e.g. `product:sku-1024`), aliases in `aliases_json`. Attach entities to chunks via `memory_remember(entities=[{id, kind, name, aliases}])`, and connect concepts via `memory_relate(source_id, target_id, relation, evidence_chunk_id=...)` — every relation should point back to the chunk that justifies it.

**3. Recall before you answer.** Before answering anything that might live in the user's memory (identity, decisions, ongoing work), call `memory_recall` — with filters when useful (`{'type': 'decision'}`, `{'source': ...}`). At session start the digest (`memory_get_digest`) already gives you the current state; expand a line with `ref` when you need the underlying detail.

**4. Consistency is the contract.** Types and kinds only pay off if used consistently. When you introduce a new kind, record the convention in your CLAUDE.md/SOUL.md (see "Adding a new entity kind" above).

---

## 🔎 Search backend (deployment matrix)

Vector index backend is **mandatory 二选一** (8/6 decision): must be `usearch` or `zvec`. `sqlite_vec` is removed as a backend; its `vectors` table stays only as legacy for migration tools.

| Backend | CPU requirement | Precision | Features | When to use |
|---|---|---|---|---|
| **zvec** | **AVX2+** (M-series / 2020+ x86_64 / modern ARM) | INT8 | HNSW + native FTS (BM25 + jieba 中文) + INT8 quantization | new CPUs (auto chain top) |
| **usearch** | any (hardware-agnostic) | **f16** | **HNSW** real ANN | old CPUs / fallback |

**Deployment rules**:
- `config.toml [search] backend = 'auto'` (default) → **zvec** if its CPU supports it, else **usearch**.
- **zvec on an unsupported CPU** (e.g. `import zvec` crashes on an old VPS) → factory detects in a **subprocess** (safe — doesn't crash the host process) and uses usearch.
- **Neither available** → `RuntimeError` — the vector library is a mandatory dependency (`pip install 'usearch>=2.26'` or `zvec`).
- **Precision is baked in** per backend (zvec=INT8, usearch=f16), not configurable.
- `scripts/health_check.py` reports the active backend + precision; `/health` surfaces maintenance recommendations when degraded.

**Switching backends** (usearch ↔ zvec): requires full re-embed of all chunks:
```bash
python scripts/rebuild_index.py --backend usearch --fresh   # full re-embed from chunks (fresh = unlink old index)
python scripts/repair_index.py --backend usearch            # remove orphan vectors (chunk gone but index not)
# Or dry-run first:
python scripts/rebuild_index.py --backend usearch --dry-run
```

> ⚠️ zvec 0.6 on pre-Ivy-Bridge x86_64 crashes on `import` — don't install it there. The factory will skip it and fall back to usearch.

---

## 📊 Benchmark results

All numbers measured on a single MacBook (M-series). Current baseline (2026-08): `memory.db` = **~44.7 MB + 0.7 MB WAL / 4,498 entities / 4,343 chunks**.

### Latency

| Metric | Value | Notes |
|---|---|---|
| **p50** | **18 ms** | warm, zvec 0.6 @ 5k vectors, 4-way concurrent (8/6 measured) |
| **p95** | **24 ms** | same |
| **p99** | **25 ms** | same |
| **p50 (15k vectors)** | **73 ms** | `scripts/benchmark.py --chunks 10000` (15422 vectors after seed) |
| **p95 (15k vectors)** | **95 ms** | HNSW scales sub-linearly with chunk count |
| **p99 (15k vectors)** | **161 ms** | worst-case observed |
| **avg (24h warm)** | 10.4 ms | `recall_log` 8/6, 232 hits, incl. cold-start outliers |
| **cold start** | ~1.1 s | MCP launch + embedder load |

Reproduce: `python scripts/benchmark.py --chunks 10000 --queries 100 --json bench.json`

### Memory footprint

One MCP server process, idle (macOS M-series): **~270 MB RSS** — of which the embedder (bge-small-zh weights + onnxruntime + tokenizer) is ~200 MB, constant regardless of data size; the rest (~70 MB) is Python + MCP + SQLite + zvec (or usearch). The 92 MB model file inflates to ~200 MB resident (float32 load + ONNX arena + tokenizer) — **file size ≠ RAM cost**. The zvec collection itself is ~30 MB on disk for 5422 512-dim vectors.

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
| **Install footprint** | 3 core pip pkgs + 1 optional + 92 MB embedding model (auto-downloaded via fastembed) | needs vector DB | ~500MB runtime | needs Neo4j etc. | needs KG stack |
| **Knowledge graph** | ✅ native | ✅ (paid tier) | – | ✅ (core) | ✅ (core) |
| **4-way RRF recall** | ✅ | – | – | – | – |
| **Bilingual (中/EN), 简/繁 classifier** | ✅ | – | – | – | – |
| **Autonomous maintenance** | ✅ (L2, audit/undo) | ✅ (LLM extraction) | ✅ (self-editing) | – | – |
| **Temporal model** | ✅ (`valid_until` + `asof`) | – | – | ✅ (bi-temporal) | – |
| **Session digest injection** | ✅ (any MCP client) | – | ✅ (core memory) | – | – |

**Honest trade-off**: the mainstream frameworks are more mature, have managed cloud tiers, and target large-scale or long-running-agent use cases. mnelo prioritizes the opposite: **simplicity, local-first, zero ops, one backup-able file**, and a knowledge graph + bilingual + autonomous maintenance that fits a personal agent — without needing to run a vector DB, a graph DB, or a full agent runtime.

If your use case is "a product serving many users / massive vector scale / a long-lived autonomous agent runtime" → Mem0/Letta/Zep are the right tools. If it's "a personal agent's memory, local, self-hosted, bilingual, with real knowledge-graph recall and self-maintenance" → that's mnelo's lane.

### Install footprint in detail

| Component | Size | Required | Notes |
|---|---|---|---|
| **Core pip packages** | ~3 MB on disk | mandatory | `mcp[cli]`, `usearch`, `fastembed` |
| **Optional pip** (`zvec`) | ~60 MB native lib | only if CPU has AVX2+ | falls back to `usearch` automatically |
| **Embedding model** | ~92 MB | mandatory for first run | `BAAI/bge-small-zh-v1.5` (CN-tuned) — auto-downloaded by fastembed into `~/.cache/huggingface/hub/`. Swap to `bge-small-en-v1.5` or `paraphrase-multilingual-MiniLM-L12-v2` via `config.toml [embedder]` |
| **Python MCP runtime** | ~50 MB | mandatory | Python 3.11 + MCP SDK + onnxruntime |
| **Vector index** | grows with data | mandatory | `usearch` is a single file; `zvec` is a directory (5422 512-dim vectors ≈ 30 MB on disk) |
| **SQLite DB** | grows with data | mandatory | one file (`memory.db`); ~45 MB at 4.5k chunks |

Total disk for a fresh install: **~200 MB** (model is the bulk). After that, growth is data-bound. No external services, no daemons to babysit, no cloud account.

---

## 🔄 Repo ↔ live sync (post-commit hook)

mnelo has two copies of every `.py` / `.sql` file: the repo (default `~/projects/mnelo/`, configurable to wherever you cloned) and the live server dir. For **hermes-agent** the conventional live dir is `~/.hermes/memory/`; for other agents it's wherever you set `MNELO_MEMORY_DIR`. The repo ships a **post-commit hook** that syncs edited files to live, backs up the old version, and runs `health_check.py` after:

```bash
# For hermes-agent:    cd ~/projects/mnelo && git config core.hooksPath .githooks
# For other agents:    cd <your-clone-dir> && git config core.hooksPath .githooks
```

Skips `memory.db` / `config.toml` / `*.md` / `tests/` (by design). Restart the MCP server after sync: `launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp`.

---

## 🌐 i18n

Add a new locale — 1 edit in `i18n_messages.py`, no code change. Set `MNELO_MEMORY_LANG=ja` to test; miss falls back to `en`, then to `msg_id` (debuggable, not silent).

---

## 🚧 Known limitations

| Limit | Workaround |
|---|---|
| **~500K vectors** @ 512-dim on a single MacBook | **zvec** (HNSW + INT8 + native FTS, AVX2+, 8/6 measured) or **usearch** (HNSW, any CPU) for real ANN |
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

- [usearch](https://github.com/unum-cloud/usearch) / [zvec](https://github.com/alibaba/zvec) — vector backends (8/6 default: `auto` chain zvec→usearch)
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — legacy vec0 table kept only for migration tools; not a runtime backend
- [fastembed](https://qdrant.github.io/fastembed) — embedder wrapper
- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) — CN embedding model
- [MCP](https://modelcontextprotocol.io) — protocol spec
- [Hermes Agent](https://nousresearch.com/hermes) — primary integration target

---

> Hermes = the messenger god.
> mnelo = his memory layer.
>
> Built 2026-07-18 by [chinesewebman](https://github.com/chinesewebman) + [Hermes Agent](https://nousresearch.com/hermes).
