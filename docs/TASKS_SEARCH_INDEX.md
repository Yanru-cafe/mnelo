# 任务分解 + 实现路径指导：SearchIndex 双后端（usearch + zvec）

> **给 hermes 的详尽执行指南**。在已落地的 `SearchIndex` 适配器（DESIGN §3.6/§8.3）上，补两条后端路径：
> **usearch**（旧 CPU 可跑的 HNSW 向量）与 **zvec**（新 CPU 的 HNSW + 原生 FTS）。
> 每项任务含：目标、改动文件、**逐方法实现指导**、精确 API 参考、边界情况、验收命令。
>
> **验证约束**：本开发机（Ivy Bridge 无 AVX2）跑不了 zvec；usearch 实测可跑。
> usearch 任务本机全验证；zvec 任务只能 fake 验证调用形状 + **Mac 部署机清单（B3）硬前置**。
> **本机已实测确认**：usearch 2.26 可 import、KNN、持久化全通过。

---

## 0. 背景与目标

mnelo 的 4 路召回中，**向量路**（`_vector_recall`）与**元数据路**（`_meta_recall`）需可选后端：

| 后端 | 向量 | 全文(meta 路) | CPU 要求 | 本机 |
|---|---|---|---|---|
| **sqlite_vec**（默认，已实现） | vec0 暴力 KNN | LIKE（无索引） | 任意 | ✅ |
| **usearch**（本任务 A 组） | **HNSW** | 保持 LIKE（FTS5 是独立 P0，不在本任务） | 任意（已实测） | ✅ |
| **zvec**（本任务 B 组） | **HNSW** | **原生 FTS（jieba）** | AVX2+ | ❌ 需 Mac |

三条路径插同一个 `SearchIndex` 接口，`[search] backend` 切换，不可用自动回落 sqlite_vec。

---

## 1. 现状代码走查（hermes 必读）

### 1.1 `search_index.py`（现有 ~250 行）

```
SearchIndex (ABC)          # name / knn / add / remove / close
├── SQLiteVecIndex         # 默认后端，已实现+测试，行为与 v0.5.x 一致
├── ZvecIndex              # 已写但未实测（content 字段空串，FTS 未填充）
├── zvec_available()       # 子进程检测（旧 CPU import 崩溃必须隔离）
├── _deserialize_f32()     # bytes(float32 序列化) → list[float]
└── build_search_index()   # 工厂：zvec 不可用回落 sqlite_vec
```

### 1.2 `memory.py` 的 `_index` 调用点（已接线，需微调）

| 位置 | 现状调用 | 本任务改动 |
|---|---|---|
| `__init__`（约 L207） | `self._index = _build_index(_cfg.search_backend, self.db_path, _cfg.embedder_dim)` | 无需改 |
| `remember()`（约 L331） | `self._index.add(chunk_id, v_bytes, conn=self._conn)` | 加 `content=content` |
| `update()`（约 L442/446） | `self._index.remove(old_id, conn=self._conn)` + `self._index.add(new_id, v_bytes, conn=self._conn)` | 加 `content=新内容` |
| `forget()`（约 L473） | `self._index.remove(target_id, conn=self._conn)` | 无需改 |
| `_vector_recall_with_conn()`（约 L680） | `self._index.knn(q_bytes, fetch_limit, conn=conn)` | 无需改 |
| `_meta_recall_with_conn()`（约 L740） | LIKE SQL 直查 | **加 supports_fts 路由** |
| `_meta_recall()` | LIKE SQL 直查 | **加 supports_fts 路由** |

### 1.3 config / health_check

- `config.py`：`self.search_backend`（env `MNELO_MEMORY_SEARCH_BACKEND` > `[search].backend` > `'sqlite_vec'`），**无枚举校验**（C1 补）
- `scripts/health_check.py`：已有 `search_backend` 检查（zvec/sqlite_vec 二分），C3 扩到三种

### 1.4 配置与回落策略（v0.2 修订：fail-fast 原则，hermes Q3 采纳）

- **显式配置了非默认后端但不可用**（未装 / CPU 不支持）→ **默认 fail-fast**：启动即报错退出，提示"backend=usearch 但 usearch 未安装；装 `requirements-usearch.txt` 或改回 sqlite_vec"
- **仅当 `MNELO_MEMORY_ALLOW_FALLBACK=1`** 时允许回落 sqlite_vec + 日志警告——用于"部分机器有后端、部分没有"的集群场景
- **理由**：silent 回落违背 §1.4 boring & predictable——主人配了 usearch 期望真用，静默降级难调试
- ⚠️ **当前已合入的 `build_search_index()`（90c158b）对 zvec 是 silent 回落——需按此修订为 fail-fast 默认**（列为 C1 的一部分）
- health_check 报告 active backend（含回落状态）；fail-fast 场景 health_check 应能诊断（而非静默）

---

## 2. 目标接口（v2，含精确签名）

```python
# search_index.py
@dataclass
class KNNHit:
    chunk_id: str
    distance: float

class SearchIndex(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def supports_fts(self) -> bool:
        """[新增] 后端是否原生支持 FTS. 默认 False."""
        return False

    @abstractmethod
    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]: ...
    #   sqlite_vec: conn 用于 vec0 MATCH + rowid→chunk_id 翻译
    #   usearch/zvec: conn 用于 chunk_id→内部id 映射查询; 索引本身忽略 conn

    @abstractmethod
    def add(self, chunk_id: str, vector_bytes: bytes, conn=None, content: Optional[str] = None) -> None:
        """[content 新增] sqlite_vec/usearch 忽略 content; zvec 填充 FTS 列."""
        ...
    #   sqlite_vec: 用 conn (同事务可见未提交 chunk), 不 commit; 无 conn 用自有连接+commit
    #   usearch:    用 conn 查 rowid 映射 (同事务可见), 向量写 usearch 索引
    #   zvec:       用 chunk_id 直接作 Doc.id (无映射), content 填 FTS 字段

    @abstractmethod
    def remove(self, chunk_id: str, conn=None) -> None:
        """幂等: 不存在也 OK."""

    def fts(self, query: str, top_k: int, conn=None) -> List[str]:
        """[新增, 默认抛错] BM25 全文检索 → top-k chunk_id (仅排序, 不做过滤).

        过滤 (valid_until/type/source/importance) 由 memory.py 在 SQLite 侧做
        (信息单源: 时间/类型权威在 chunks 表). 不支持的后端抛 NotImplementedError.
        """
        raise NotImplementedError(f"{self.name} does not support FTS")

    @abstractmethod
    def close(self) -> None:
        """释放资源 + 持久化落盘."""
```

**conn 语义铁律**：`add/remove` 传了 conn 用调用方连接（同事务、可见未提交 chunk）、不 commit；不传用自有连接 + 自身 commit。`knn` 传 conn 用 lane 连接。**勿破坏 sqlite_vec 已修好的行为**。

---

## 3. 依赖与安装

```bash
# usearch（本机已实测可跑）— 可选
pip install "usearch>=2.26"
# 建 requirements-usearch.txt (C1)

# zvec（需 AVX2+ CPU）— 可选，已有 requirements-zvec.txt
pip install -r requirements-zvec.txt
```

---

## 4. 任务组 A：usearch 后端（本机可实测）

### A1 — `usearch_available()` 进程内检测
```python
def usearch_available() -> bool:
    """usearch 在旧 CPU 不崩 (已实测), 只有 ImportError 可能 → 进程内 try 即可."""
    try:
        import usearch  # noqa: F401
        return True
    except ImportError:
        return False
```
**验收**：本机 `usearch_available() == True`。

### A2 — `UsearchIndex` 类（逐方法实现指导）

```python
class UsearchIndex(SearchIndex):
    """usearch 后端 — HNSW, 硬件无关 (旧 CPU 可跑, DESIGN §8.3 升级档)."""

    def __init__(self, db_path: Path, dim: int):
        self.db_path = db_path
        self.dim = dim
        self._index_path = db_path.parent / "usearch.index"
        # 映射查询用自有 sqlite 连接 (usearch 不在 memory.py 事务里)
        self._conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # usearch 索引: 已存在则 load, 否则新建
        from usearch.index import Index
        self._index = Index(ndim=dim, metric='cos')
        if self._index_path.exists():
            self._index.load(self._index_path)   # 注意: load 是实例方法, 不是类方法

    @property
    def name(self) -> str: return "usearch"
    @property
    def supports_fts(self) -> bool: return False

    def knn(self, query_bytes, top_k, conn=None):
        import numpy as np
        from usearch.index import Index  # noqa: F401
        vec = np.array([_deserialize_f32(query_bytes)], dtype=np.float32)
        res = self._index.search(vec, top_k)
        c = conn or self._conn
        hits = []
        for uid, dist in zip(res.keys, res.distances):   # uid 是 np.uint64 (chunks.rowid)
            row = c.execute("SELECT id FROM chunks WHERE rowid = ?", (int(uid),)).fetchone()
            if row:
                hits.append(KNNHit(chunk_id=row["id"], distance=float(dist)))
        return hits

    def add(self, chunk_id, vector_bytes, conn=None, content=None):
        import numpy as np
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            import logging; logging.getLogger("mnelo.index").warning(f"[usearch.add] chunk {chunk_id} not found")
            return
        vec = np.array([_deserialize_f32(vector_bytes)], dtype=np.float32)
        ids = np.array([row["rowid"]], dtype=np.uint64)   # ⚠️ 必须 uint64, 已实测 int 会报错
        self._index.add(ids, vec)

    def remove(self, chunk_id, conn=None):
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row:
            self._index.remove([np.uint64(row["rowid"])])   # 幂等 (usearch 容忍不存在)

    def close(self):
        self._index.save(self._index_path)   # 持久化
        self._conn.close()
```

**usearch API 参考（实测确认）**：
| 调用 | 注意 |
|---|---|
| `Index(ndim=4, metric='cos')` | 新建 |
| `idx.add(np.array([ids], dtype=np.uint64), np.array([vecs], dtype=np.float32))` | **必须 numpy + float32 + uint64**，Python list/int 会 AssertionError |
| `res = idx.search(np.array([vec], dtype=np.float32), k)` | `res.keys` / `res.distances` |
| `idx.save(path)` / `idx2.load(path)` | load 是**实例方法**：`idx2 = Index(...); idx2.load(path)` |
| `idx.hardware_acceleration` | 可作健康检查诊断输出 |

**验收**：本机 add 2 条 → knn 命中正确顺序 → remove 后不再命中 → close/reopen 数据仍在。

### A3 — id 映射（chunk_id ↔ rowid）

- **内部 id = `chunks.rowid`**（隐式整数主键，TEXT 主键表下稳定），与 sqlite_vec 同构
- **无独立映射表**——直接查 SQLite，避免双写不一致
- 映射查询用 `conn or self._conn`：memory.py 传 conn 时同事务可见未提交 chunk；不传时自有连接只看已提交（独立场景够用）

### A4 — 工厂接入（含 fail-fast 回落策略）
```python
import os
_ALLOW_FALLBACK = os.environ.get("MNELO_MEMORY_ALLOW_FALLBACK") == "1"

def build_search_index(backend: str, db_path: Path, dim: int) -> SearchIndex:
    if backend == "zvec":
        if not zvec_available():
            if not _ALLOW_FALLBACK:
                raise RuntimeError(
                    "backend=zvec 但 zvec 不可用 (未装或 CPU 不支持 AVX2+)。"
                    "装 requirements-zvec.txt 或改 [search] backend, 或设 MNELO_MEMORY_ALLOW_FALLBACK=1")
            logger.warning("[search_index] zvec 不可用, ALLOW_FALLBACK 回落 sqlite_vec")
            return SQLiteVecIndex(db_path)
        return ZvecIndex(db_path.parent / "search_index.zv", dim)
    if backend == "usearch":
        if not usearch_available():
            if not _ALLOW_FALLBACK:
                raise RuntimeError(
                    "backend=usearch 但 usearch 未安装。装 requirements-usearch.txt "
                    "或改 [search] backend, 或设 MNELO_MEMORY_ALLOW_FALLBACK=1")
            logger.warning("[search_index] usearch 未安装, ALLOW_FALLBACK 回落 sqlite_vec")
            return SQLiteVecIndex(db_path)
        return UsearchIndex(db_path, dim)
    return SQLiteVecIndex(db_path)
```
- **同时修订现有 zvec 分支**（90c158b 合入的是 silent 回落）→ 统一 fail-fast + ALLOW_FALLBACK
- **验收**：本机 `backend='usearch'` 且装了 → `name == 'usearch'`；未装且无 ALLOW_FALLBACK → `RuntimeError`；有 ALLOW_FALLBACK → 回落 sqlite_vec。

### A5 — usearch 真实单测（`tests/test_search_index.py` 追加）
```python
class TestUsearchIndex(unittest.TestCase):
    def setUp(self): self.idx = UsearchIndex(resolve_db_path(), 512)
    def tearDown(self): self.idx.close()
    def test_01_knn_roundtrip(self):
        # remember 两条 → vector_only recall 命中 → remove 后不命中
        ...
    def test_02_persistence(self):
        # close 后新实例 reopen → 仍命中
        ...
    def test_03_idempotent_remove(self):
        self.idx.remove("nonexistent_id")  # 不抛
    def test_04_memory_integration(self):
        # MNELO_MEMORY_SEARCH_BACKEND=usearch 下 remember → recall(vector_only) 命中
        ...
```

### A6 — 后端切换重建索引脚本（新，`scripts/rebuild_index.py`）
```python
# 切后端后向量索引为空 → 从 chunks 全量重嵌
# usage: python scripts/rebuild_index.py [--backend usearch|zvec]
#   1. 遍历 chunks (valid_until IS NULL)
#   2. embed → index.add(chunk_id, v_bytes, conn=main_conn, content=content)
#   3. 报告总数/失败数
```
**验收**：sqlite_vec → usearch 切换后跑此脚本，recall 命中率恢复。

### A7 — 索引修复 + 完整性校验（新，`scripts/repair_index.py`；hermes Q1/Q2 采纳）

**问题背景**：
- **Q1 孤儿向量**：usearch/zvec 索引写入在 SQLite 事务外——若 remember 的 SQLite 侧最终 ROLLBACK（commit 失败/异常），索引里会留下指向不存在 chunk 的向量
- **Q2 双写非原子**：`close()` 的 `index.save()` 与 SQLite checkpoint 是两个独立 IO——save 成功但 SQLite 侧异常 → 下次启动索引与库不一致

**做**：
1. `scripts/repair_index.py [--backend usearch|zvec] [--dry-run]`：
   - 遍历索引内所有 id → 查 SQLite chunks（usearch 用 rowid、zvec 用 chunk_id）→ **删除无对应活跃 chunk 的索引项**（仿 `repair_vectors.py`）
   - `--dry-run` 只报数不删
2. **索引完整性校验（启动时）**：
   - `UsearchIndex.close()` / `ZvecIndex.close()` save 时，写 sidecar（如 `usearch.index.checksum`）：源 chunk 计数 + 哈希
   - `__init__` load 后校验 sidecar → 失配 → `logger.warning` + 建议跑 repair/rebuild
3. **auto-repair（v0.2，hermes 二轮 Q1 采纳）**：`MNELO_MEMORY_AUTO_REPAIR_INDEX=1` 时，sidecar 失配**自动跑 repair_index.py**（仅孤儿删除，安全操作），并记录修复动作。⚠️ 注意：这**不是**镜像已有模式——代码库中不存在 `MNELO_MEMORY_AUTO_REPAIR_VECTORS`，这是确立新模式。设计护栏：auto-repair **只做孤儿删除**（不重建、不删活跃项），必须显式 env 开启
4. **drift 指标（独立补充）**：health_check 报 `index_drift` = 索引孤儿数 / 活跃 chunks 数——让漂移**持续可观测**（hermes 二轮担心"失配会无限累积"，drift 指标让它在 recall 崩之前就被看到），与 auto-repair 互补：drift 负责"看见"，repair 负责"清掉"

**验收**：构造孤儿场景（手动删 SQLite chunk 不动索引）→ `repair_index.py --dry-run` 报出、实际跑后清掉；sidecar 篡改 → 启动警告；设 `MNELO_MEMORY_AUTO_REPAIR_INDEX=1` → 启动自动修复 + 日志；health_check 显示 drift 指标。

---

## 5. 任务组 B：zvec 后端补全 + 验证

> ⚠️ 本机不可跑 zvec（CPU 不支持）。B1/B2 只能以 fake zvec 验证调用形状；**B3 部署机清单未过前不得默认启用**。

### B1 — `ZvecIndex` 补全（逐方法 + zvec API 参考）

**现有 `ZvecIndex` 审查点**：`_zvec` 实例属性（修过 NameError）、`create_and_open`、schema（embedding + content + memory_type + source）、HNSW 索引。**缺**：`content` 未填充、`supports_fts`、`fts()`、`add` 的 content 参数。

**zvec 0.6 API 参考（从 .pyi 确认）**：
| 调用 | 签名要点 |
|---|---|
| `zvec.create_and_open(path, zvec.CollectionOption())` | 返回 Collection |
| `zvec.open(path, CollectionOption())` | 已有则打开 |
| `schema = zvec.CollectionSchema()` | `add_dense_vector_field(name, dim)` / `add_text_field(name)` |
| `col.create(schema)` → `col.create_index(field_name='embedding', index_type='HNSW')` | 建索引 |
| `col.upsert(zvec.Doc(id=str, fields={'content': ...}, vectors={'embedding': [float]}))` | **id 是 str，直接用 chunk_id，无映射表** |
| `col.delete([id])` | 幂等 |
| `col.query(zvec.Query(field_name='embedding', vector=[float], param=zvec.HnswQueryParam(ef=n)))` | → list[Doc]，`.id` / `.score` |
| `col.query(zvec.Query(field_name='content', fts=zvec.Fts(match_string=...), param=zvec.FtsQueryParam(default_operator='AND')))` | FTS 查询（B2 用） |
| `col.flush()` | close 时落盘 |

**改动**：
```python
class ZvecIndex(SearchIndex):
    def __init__(self, collection_path: Path, dim: int):
        # 现有逻辑 + self._index_path = collection_path
    @property
    def supports_fts(self) -> bool: return True

    def add(self, chunk_id, vector_bytes, conn=None, content=None):
        zv = self._zvec
        self._col.upsert(zv.Doc(
            id=chunk_id,                                   # str, 无映射
            fields={"content": content or ""},             # 填充 FTS 列
            vectors={"embedding": _deserialize_f32(vector_bytes)},
        ))

    def fts(self, query, top_k, conn=None):
        zv = self._zvec
        docs = self._col.query(zv.Query(
            field_name="content",
            fts=zv.Fts(match_string=query),
            param=zv.FtsQueryParam(default_operator="AND"),
        ))
        return [d.id for d in docs[:top_k]]                 # 只返回 chunk_id, 过滤在 SQLite
```
**验收**：fake zvec 下 `add` 带 content、`Query(vector=...)` / `Query(fts=...)` 调用形状正确。

### B2 — zvec 原生 FTS 集成（meta 路路由）

**`memory.py` 两处 `_meta_recall*` 改路由**：
```python
def _meta_recall_with_conn(self, conn, query, top_k, filters, asof):
    if self._index.supports_fts:
        # FTS 后端 (zvec): 先 BM25 取 top-N, 再 SQLite 过滤+重排
        ids = self._index.fts(query, top_k * 4, conn=conn)   # 多取 margin
        if not ids: return []
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, content, memory_type, source, timestamp, importance FROM chunks "
            f"WHERE id IN ({ph}) AND (valid_until IS NULL OR valid_until > ?) "
            f"ORDER BY importance DESC, timestamp DESC LIMIT ?",
            [*ids, asof, top_k],
        ).fetchall()
        return [self._hit_dict(r, method="meta") for r in rows]
    # 非 FTS 后端: 现有 LIKE SQL (不动)
    ...
```
- **信息单源**：zvec 只管 BM25 排序；valid_until/type/source 过滤 + importance 排序全在 SQLite（与 §3.0.6 一致：type 硬过滤对 chunk 路有效）
- **排序语义变化**：FTS 路径 = BM25 粗排 → importance/timestamp 精排；非 FTS 路径保持纯 LIKE。**接受**（FTS 是升级）
- `filters['type']`/`['source']` 在下述 SQL 里补（复用现有 filters 逻辑）

**验收**：fake zvec 下 `_meta_recall` 走 FTS 分支、非 FTS 后端走 LIKE 分支（本机用 sqlite_vec 验证分支正确）。

### B3 — zvec 部署机验证清单（Mac，硬前置）
```bash
# 1. 安装
pip install -r requirements-zvec.txt
# 2. 启后端 + 健康检查
MNELO_MEMORY_SEARCH_BACKEND=zvec python scripts/health_check.py
#   期望: "✅ Search backend — zvec"
# 3. 小规模基准 (向量命中率)
python scripts/benchmark.py --chunks 1000
# 4. 中文 FTS 抽查
#    写入含 "建仓" 的 chunk → recall(..., strategy='meta_only') 按 "建仓" 命中
# 5. 持久化抽查: 重启 MCP server 后 recall 正常
# 6. 回退抽查: 停掉 zvec (卸载/设 sqlite_vec) → health_check 报回落, 不崩
```
**全过 → zvec 后端可启用**。

---

## 6. 任务组 C：通用接入

### C1 — config 枚举校验 + requirements 拆分
```python
# config.py
_ALLOWED_SEARCH_BACKENDS = ("sqlite_vec", "usearch", "zvec")
... self.search_backend = <现有解析>
if self.search_backend not in _ALLOWED_SEARCH_BACKENDS:
    print(f"[config] WARN: search_backend {self.search_backend!r} 非法, 回落 sqlite_vec", file=sys.stderr)
    self.search_backend = "sqlite_vec"
```
- 新 `requirements-usearch.txt`：`usearch>=2.26`
- `requirements.txt` / README：三个可选后端各自装法（参照 §0 矩阵）

### C2 — 接口演进 + memory.py 改动点（**先行，A/B 依赖它**）
- `search_index.py`：加 `supports_fts`（默认 False）+ `fts()`（默认抛 NotImplementedError）+ `add` 加 `content=None`
- `memory.py`：
  - `remember()`：`self._index.add(chunk_id, v_bytes, conn=self._conn, content=content)`
  - `update()`：两处 add 补 `content=新内容`
  - `_meta_recall*`：按 `supports_fts` 路由（B2 的模式，非 FTS 后端走现有 LIKE）
- **验收**：sqlite_vec 后端 `supports_fts=False`，meta 路仍 LIKE（回归安全）；现有 563+ 测试全绿

### C3 — health_check 三后端报告
```python
sb = report["checks"].get("search_backend", {})
want = _cfg.search_backend
if want == "zvec":   ok = zvec_available()
elif want == "usearch": ok = usearch_available()
else:                ok = True
# 输出: configured / active / available; 不可用 → degraded + 回落提示
```

### C4 — 全量回归
`pytest tests/` 全绿（默认 sqlite_vec 零破坏）；已知环境失败（port/echo/stats 数据依赖）除外。

---

## 7. 执行顺序（依赖驱动）

```
C1 (config/requirements) ─┐
C2 (接口 v2 + memory.py) ─┴─→ A1→A2→A3→A4→A5→A6→A7  (usearch, 本机全测)
                         └──→ B1→B2 → B3 (zvec, Mac 验证)
C3 (health_check 三后端) ←─  A/B 完成
C4 (全量回归)              ←─  全部
```

**建议分批 commit**（小步，hermes 偏好）：
1. `C1+C2`：接口演进 + 配置（无新后端，纯重构，回归兜底）
2. `A1-A5`：usearch 后端 + 本机测试
3. `A6`：重建索引脚本
4. `B1-B2`：zvec 补全 + FTS（fake 验证）
5. `C3`：health_check
6. `B3`：Mac 验证（合入前）

---

## 8. 验收标准（整体）

1. `[search] backend` 三值可切换；**显式配置不可用 → fail-fast（默认）**，`ALLOW_FALLBACK=1` 才回落 + 日志（§1.4）
2. **usearch**：本机 A5 全过；A6 重建后命中恢复
3. **zvec**：fake 形状验证 + B3 Mac 清单全过
4. **sqlite_vec**：默认路径零回归（C4）
5. health_check 正确报告三后端（C3）
6. 文档/requirements 同步（C1）

---

## 9. 风险与边界

| 风险 | 缓解 |
|---|---|
| 双存储同步（usearch/zvec 文件 vs SQLite） | 索引只存向量+content；时间/类型/软删权威在 SQLite（查询时过滤）；`close()` save + A6 重建 + **A7 增量修复**兜底 |
| **Q1 孤儿向量**（索引写入在 SQLite 事务外，rollback 后索引残留） | 接受窄窗口（仅 commit 失败时）；**A7 `repair_index.py`** 定期清理（仿 repair_vectors.py） |
| **Q2 close() 双写非原子**（save 与 checkpoint 独立 IO） | **A7 sidecar 校验和**（save 时记源 chunk 哈希，load 时比对，失配警告引导 repair/rebuild）；快照恢复须同点恢复 SQLite + 索引文件 |
| usearch rowid 映射稳定性 | 与 sqlite_vec 同假设（TEXT 主键表 rowid 稳定）；快照恢复后 rowid 不变；VACUUM 重建场景跑 A6 |
| zvec 本机无法验证 | B3 为硬前置，未过不得默认启用 |
| usearch/zvec 无事务（crash 窗口不一致） | close() save + 定期 save + 快照恢复后重建 |
| 接口签名变更（add 加 content） | C2 先行 + 全量回归兜底 |
| FTS5（meta 路升级）不在本任务 | 独立 P0（DESIGN §4.1）；zvec FTS 是本任务的 zvec 专属路径 |

---

## 10. 参考文件
- `search_index.py`（接口 + SQLiteVecIndex + ZvecIndex + 工厂 + zvec_available）
- `memory.py`（§1.2 的 7 个 `_index` 调用点）
- `tests/test_search_index.py`（fake zvec 模式 + 现有测试）
- `config.py` / `scripts/health_check.py` / `requirements*.txt`
- `docs/DESIGN.md` §3.6/§3.0.6/§4.1/§8.3
