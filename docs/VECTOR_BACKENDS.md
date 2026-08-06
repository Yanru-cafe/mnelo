# Vector backends

mnelo has 2 supported runtime vector backends
([DESIGN §3.6/§8.3](DESIGN.md)):

| Backend | Quantization | When |
|---|---|---|
| **zvec** | INT8 | Modern CPUs (M-series / AVX2+); M2 measured 5k vectors p50=18ms |
| **usearch** | f16 | Any CPU — fallback when zvec unavailable |

The `build_search_index()` factory detects via **main-process import**
(macOS 26 launchd forks hit `BlockingIOError` running zvec native mmap,
so we import in-process): zvec installed → zvec; otherwise usearch. If
neither is installed → `RuntimeError` (a vector library is a mandatory
dependency). **Default `auto` chain** — `config.toml [search] backend =
'auto'`; override via env `MNELO_MEMORY_SEARCH_BACKEND=zvec|usearch`.

`scripts/health_check.py` reports the active backend; `/health` advises
when it downgrades.

## Switching backends

(zvec ↔ usearch; index formats are not interchangeable) requires a full
re-embed from chunks:

```bash
python scripts/rebuild_index.py --backend zvec      # full re-embed
python scripts/repair_index.py --backend zvec       # cleanup orphan vectors
# or dry-run first:
python scripts/rebuild_index.py --backend zvec --dry-run
```

## Precision contract (2026-08-06): usearch is permanently f16

The `usearch.index` file is f16-format — every load/rebuild/test must
explicitly use `Index(dtype='f16')`; a default (f32) `Index` loading an
f16 file crashes natively (`free(): corrupted unsorted chunks` — a test
artifact, not a data problem). UsearchIndex enforces f16 with both init
and load-time assertions:

- `Index(ndim=dim, metric="cos", dtype="f16")` — `__init__` runtime guard
- `if self._index.dtype != ScalarKind.F16` after `.load()` — load-time guard

## Startup crash triage

`free(): corrupted unsorted chunks` / `Aborted` → corrupt index.

If `usearch.index` was written abnormally and is stale relative to the
`chunks` table (holds rowids no longer in the DB), the MCP server
blind-loads it at startup and aborts (symptom: crash right after `[H-1]`,
before `mnelo MCP ready`, port not listening). Check and rebuild
(**data-safe** — chunks live in SQLite):

```bash
sqlite3 $MNELO_MEMORY_DIR/memory.db "SELECT COUNT(*) FROM chunks WHERE valid_until IS NULL"   # confirm valid chunk count
cp $MNELO_MEMORY_DIR/usearch.index $MNELO_MEMORY_DIR/usearch.index.stale                      # back up bad index
python scripts/rebuild_index.py --backend usearch --fresh    # full re-embed of valid chunks, same bge-small-zh-v1.5 model
```

After rebuild, `health_check.py`'s `vectors` count should equal valid
chunks.

## CPU detection — AVX2

```bash
# macOS
sysctl machdep.cpu.features | grep -o AVX2
# Linux
grep -m1 -o avx2 /proc/cpuinfo
```

- AVX2 present → `zvec` is a safe choice (adds native full-text + INT8 quantization)
- AVX2 absent → `usearch` only (do **not** install zvec; it crashes on
  `import` on these CPUs)
