# Why mnelo? (vs the mainstream agent-memory landscape)

mnelo sits in a different lane from the big agent-memory frameworks.
**Mem0 / Letta / Zep / Cognee** are built for *product-scale deployments*
— managed services or self-hosted servers, often requiring external
vector/graph DBs or a full agent runtime. **mnelo is the local-first,
single-file, zero-cloud option**: one SQLite file, standard MCP, works
with any agent, bilingual out of the box, with an optional autonomous
maintenance layer.

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

**Honest trade-off**: the mainstream frameworks are more mature, have
managed cloud tiers, and target large-scale or long-running-agent use
cases. mnelo prioritizes the opposite: **simplicity, local-first, zero
ops, one backup-able file**, and a knowledge graph + bilingual +
autonomous maintenance that fits a personal agent — without needing to
run a vector DB, a graph DB, or a full agent runtime.

If your use case is "a product serving many users / massive vector scale
/ a long-lived autonomous agent runtime" → Mem0/Letta/Zep are the right
tools. If it's "a personal agent's memory, local, self-hosted,
bilingual, with real knowledge-graph recall and self-maintenance" →
that's mnelo's lane.

## Install footprint in detail

| Component | Size | Required | Notes |
|---|---|---|---|
| **Core pip packages** | ~3 MB on disk | mandatory | `mcp[cli]` (MCP server + SSE transport), `usearch` (vector library fallback), `fastembed` (embedding loader) |
| **Tool-helper pip** (`sqlite-vec`) | ~1 MB | needed for tools only | `vec0` virtual table for `migrate` / `repair` / `init_db` scripts — not a runtime backend |
| **Optional pip** (`zvec`) | ~60 MB native lib | only if CPU has AVX2+ | HNSW + native FTS + INT8; falls back to `usearch` automatically when absent |
| **Embedding model** | ~92 MB | mandatory for first run | `BAAI/bge-small-zh-v1.5` (CN-tuned) — auto-downloaded by fastembed into `~/.cache/huggingface/hub/`. Swap to `bge-small-en-v1.5` or `paraphrase-multilingual-MiniLM-L12-v2` via `config.toml [embedder]` |
| **Python MCP runtime** | ~50 MB | mandatory | Python 3.11 + MCP SDK + onnxruntime |
| **Vector index** | grows with data | mandatory | `usearch` is a single file; `zvec` is a directory (5422 512-dim vectors ≈ 30 MB on disk) |
| **SQLite DB** | grows with data | mandatory | one file (`memory.db`); ~45 MB at 4.5k chunks |

Total disk for a fresh install: **~200 MB** (model is the bulk). After
that, growth is data-bound. No external services, no daemons to
babysit, no cloud account.
