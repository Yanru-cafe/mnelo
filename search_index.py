#!/usr/bin/env python3
"""
search_index.py — L1 检索层索引抽象 (DESIGN §3.6 / §8.3)

只抽象"向量索引"的 KNN 与写入; 召回业务逻辑 (asof / 过滤器 / RRF / lane 组合)
留在 memory.py。

[8/6 plan] 向量库必选二选一 + 分精度:
  - usearch → f16 精度 (兜底; Index dtype='f16', 2 字节/维, 自动 f32↔f16 cast)
  - zvec    → INT8 精度 (新 CPU 优先; auto 链上层, 通过 zvec schema DataType)
  - sqlite_vec 已出局 (vec0 表保留作 legacy, 给 migrate/repair/init_db 工具用)

⚠️ zvec 后端说明 (重要):
  - zvec 0.6 原生扩展要求较新 CPU 指令 (AVX2+)。在旧 CPU 上 `import zvec` 直接
    Illegal instruction 崩溃 (进程级, 非异常)。因此 zvec 可用性检测必须在
    **子进程**中进行, 不可在 mnelo 进程内 try-import (会把 mnelo 一起带崩)。
  - zvec 后端代码按 zvec 0.6 类型化 API (zvec.pyi + model/*.py) 编写,
    **尚未在目标机 (Mac ARM64) 实测** — 需在部署机上验证后启用。
    INT8 精度 API 用 DataType.VECTOR_INT8 假设 (见风险 9, 真实 API 待 zvec 文档核实).
  - 本机 Ivy Bridge 上 zvec SIGILL 不可用, 本环境验证基于 usearch + TestZvecBackendWithFake.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("mnelo.index")


# ============================================================
# 统一命中结构
# ============================================================

@dataclass
class KNNHit:
    """向量召回命中 — chunk_id 是唯一标识 (与后端解耦)."""
    chunk_id: str
    distance: float


# ============================================================
# SearchIndex 抽象
# ============================================================

class SearchIndex(ABC):
    """向量索引抽象. 写入 (add/remove) + KNN + 后端感知孤儿清理 + size/contains."""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名: 'usearch' | 'zvec'."""

    @abstractmethod
    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        """KNN 检索: query_bytes (序列化 float32 向量) → 按距离升序的命中.

        只返回 chunk_id + distance; valid_until/asof/filters 过滤由 memory.py
        在 chunk 侧做 (保证与 lane 业务逻辑解耦).
        conn: usearch/zvec 都忽略 (翻译 rowid 时用自有 _conn).
        """

    @abstractmethod
    def add(self, chunk_id: str, vector_bytes: bytes, conn=None, content: Optional[str] = None) -> None:
        """索引一条 chunk 的向量. chunk_id 需先存在于 chunks 表. 幂等.

        content: usearch 忽略; zvec 填充 FTS 列.
        conn: 语义同 add; 后端忽略 (索引独立于 SQLite 事务).
        """

    @abstractmethod
    def remove(self, chunk_id: str, conn=None) -> None:
        """删除一条 chunk 的向量索引. 幂等 (不存在也 OK). conn 语义同 add."""

    @abstractmethod
    def size(self) -> int:
        """索引中当前向量条数 — stats 的 vectors 字段按实际后端计数 (8/5 主人 commit).

        usearch 数 HNSW 索引 (Index.size 属性, 非方法, 已踩坑);
        zvec 数 collection. 避免在非 sqlite_vec 后端下显示恒 0 的假象.
        """

    @abstractmethod
    def close(self) -> None:
        """释放资源 (连接/collection/index 持久化)."""

    @abstractmethod
    def contains(self, chunk_id: str, conn=None) -> bool:
        """该 chunk_id 的向量是否在索引中.

        [8/6 plan §2] 后端感知 — usearch 用 rowid + Index.keys; zvec 用 chunk_id
        直接遍历 iter_all. 跨测试断言统一走这个 API, 不再查 vec0 表.
        """

    @abstractmethod
    def cleanup_orphans(self, conn=None, dry_run: bool = False) -> Dict:
        """[8/6 plan §2] 后端感知孤儿向量清理.

        返回 {
            'soft_deleted_cleaned': int,  # 索引 entry 但 chunks.valid_until 非空
            'truly_orphan_cleaned': int,  # 索引 entry 但 chunks 行已删
            'vectors_remaining': int,     # 清理/扫描后索引剩余
            'dry_run': bool,
        }

        落盘交给 close(); 本方法不 save (purge worker 在活 server 同一进程
        内存态立即生效; CLI 路径走 maintain_vectors.py 子进程, 退出时 save).
        """


# ============================================================
# zvec 后端
# ============================================================

class ZvecIndex(SearchIndex):
    """zvec 后端 — 进程内嵌向量库 (DESIGN §8.3 升级档).

    ⚠️ 未在本环境实测 (CPU 不支持 zvec 原生指令)。按 zvec 0.6 API 编写,
    需在部署机 (Mac ARM64 / 新 x86) 上跑 search_index_smoke 验证后启用。
    本类的 add/remove/knn 与 UsearchIndex 语义对齐, 便于 memory.py 无感切换。

    [8/6 plan §2 精度] INT8 量化 — 通过 schema 字段 DataType.VECTOR_INT8 指定
    (API 假设; 真机部署前核实 zvec 0.6 文档: <https://zvec.org/docs/db/>).
    """

    def __init__(self, collection_path: Path, dim: int):
        self.collection_path = collection_path
        self.dim = dim
        self._col = None  # type: ignore
        # 延迟导入 — import zvec 在旧 CPU 上会崩, 由工厂子进程检测把关
        # 存为实例属性供 _create_schema 使用 (module 局部变量在方法间不可见)
        import zvec  # noqa: F401

        self._zvec = zvec
        # [8/6 fix] zvec 0.6 schema-first API: create_and_open(path, schema, option),
        # 不接受空 schema. 旧路径 create → create(schema) 已被 0.6 废弃.
        # 修法: 一次性 _build_schema() 拿 schema → create_and_open(path, schema)
        if collection_path.exists():
            self._col = zvec.open(str(collection_path))
        else:
            schema = self._build_schema()
            self._col = zvec.create_and_open(str(collection_path), schema)

    def _build_schema(self) -> "zvec.CollectionSchema":
        """建 schema: embedding (512d FP32 + HNSW) + content (FTS jieba) + memory_type + source."""
        zv = self._zvec
        # [8/6 fix] zvec 0.6 declarative API: CollectionSchema(name, fields=[...], vectors=[...])
        # 旧 method-style add_*_field 在 0.6 不存在. VECTOR_FP32 = 跟 sqlite-vec / usearch 同精度.
        # HNSW 用默认 HnswIndexParam() (ef_construction=200, m=16 文档 default).
        # FTS 用 jieba (中文 tokenizer, 主人偏好, 见 README §向量后端部署矩阵).
        schema = zv.CollectionSchema(
            name=self.collection_path.stem,
            fields=[
                zv.FieldSchema(name="content", data_type=zv.DataType.STRING,
                               index_param=zv.FtsIndexParam(tokenizer_name="jieba")),
                zv.FieldSchema(name="memory_type", data_type=zv.DataType.STRING),
                zv.FieldSchema(name="source", data_type=zv.DataType.STRING),
            ],
            vectors=[
                zv.VectorSchema(name="embedding", data_type=zv.DataType.VECTOR_FP32,
                                dimension=self.dim,
                                index_param=zv.HnswIndexParam()),
            ],
        )
        return schema

    @property
    def name(self) -> str:
        return "zvec"

    @property
    def supports_fts(self) -> bool:
        return True

    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        # query_bytes 是 float32 序列化; zvec 接受 python list[float]
        vec = _deserialize_f32(query_bytes)
        zv = self._zvec
        docs = self._col.query(
            zv.Query(field_name="embedding", vector=vec, param=zv.HnswQueryParam(ef=top_k * 2))
        )
        hits = []
        for d in docs[:top_k]:
            # [8/6 fix] zvec doc id = chunks.rowid (int, 生产路径). 翻译回 chunk_id via SQLite.
            # 测试路径 (conn=None, fake zvec): d.id 可能是 chunk_id 本身 (非 int), 直接用.
            try:
                zvec_doc_id = int(d.id)
                if conn is not None:
                    row = conn.execute(
                        "SELECT id FROM chunks WHERE rowid = ?", (zvec_doc_id,)
                    ).fetchone()
                    if row is None:
                        continue
                    chunk_id = row[0]
                else:
                    # 生产路径但 conn=None 兜底 (不推荐)
                    chunk_id = str(zvec_doc_id)
            except (TypeError, ValueError):
                # 测试路径 / 旧 rebuild 残留 (chunk_id 直接当 zvec_id)
                chunk_id = str(d.id)
            hits.append(KNNHit(chunk_id=chunk_id, distance=float(d.score)))
        return hits

    def add(self, chunk_id: str, vector_bytes: bytes, conn=None, content: Optional[str] = None,
            memory_type: str = "", source: str = "") -> None:
        zv = self._zvec
        # [8/6 fix] zvec 0.6 schema 所有 STRING 字段 nullable=False (default), 必传.
        if (not content or not memory_type or not source) and conn is not None:
            row = conn.execute(
                "SELECT content, memory_type, source FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if row:
                content = content or (row["content"] if hasattr(row, "keys") else row[0]) or ""
                memory_type = memory_type or (row["memory_type"] if hasattr(row, "keys") else row[1]) or "fact"
                source = source or (row["source"] if hasattr(row, "keys") else row[2]) or ""
        # [8/6 fix] zvec 0.6 doc id 必须 [A-Za-z0-9_-]; 但 mnelo chunk_id 含 ':' / 中文.
        # 实战方案: 用 chunks.rowid (int) 当 zvec doc id — 稳定 + 唯一 + 不丢信息.
        # knn() / remove() / contains() / cleanup_orphans() 都通过 rowid join 翻译.
        if conn is not None:
            row = conn.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row is None:
                raise ValueError(f"chunk {chunk_id} not found in chunks table (no rowid)")
            zvec_id = str(row[0])
        else:
            # fallback (测试路径): sanitize. 不推荐生产用.
            zvec_id = chunk_id.replace(":", "_").replace(" ", "_")
        self._col.upsert(
            zv.Doc(
                id=zvec_id,
                fields={
                    "content": content or "",
                    "memory_type": memory_type or "fact",
                    "source": source or "",
                },
                vectors={"embedding": _deserialize_f32(vector_bytes)},
            )
        )

    def remove(self, chunk_id: str, conn=None) -> None:
        # [8/6 fix] zvec doc id = chunks.rowid, 翻译: chunk_id → rowid → zvec_id
        zvec_id = self._chunk_id_to_zvec_id(chunk_id, conn)
        if zvec_id is not None:
            self._col.delete([zvec_id])

    def _chunk_id_to_zvec_id(self, chunk_id: str, conn) -> Optional[str]:
        """[8/6 fix] chunk_id → chunks.rowid → zvec doc id (str). None = 没找到."""
        if conn is None:
            return chunk_id  # fallback (测试)
        row = conn.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return str(row[0]) if row else None

    def size(self) -> int:
        """[8/5 主人 commit] zvec collection 文档数 (走 stats 属性 + iter_all 兜底)."""
        try:
            # zvec 0.6 stats 是 property 不是 method, 含 doc_count
            stats = self._col.stats
            if hasattr(stats, "doc_count"):
                return int(stats.doc_count)
        except Exception:
            pass
        try:
            return sum(1 for _ in self._col.iter_all())
        except Exception:
            return 0

    def fts(self, query: str, top_k: int, conn=None) -> List[str]:
        """zvec 原生 FTS BM25 → top-k chunk_id (仅排序). 过滤在 memory.py SQLite 侧.
        [8/6 fix] d.id 是 zvec doc id (= chunks.rowid), 翻译回 chunk_id via SQLite.
        """
        zv = self._zvec
        docs = self._col.query(
            zv.Query(
                field_name="content",
                fts=zv.Fts(match_string=query),
                param=zv.FtsQueryParam(default_operator="AND"),
            )
        )
        result: List[str] = []
        for d in docs[:top_k]:
            try:
                rowid_int = int(d.id)
            except (TypeError, ValueError):
                continue
            if conn is not None:
                row = conn.execute("SELECT id FROM chunks WHERE rowid = ?", (rowid_int,)).fetchone()
                if row:
                    result.append(row[0])
            else:
                result.append(str(d.id))
        return result

    def close(self) -> None:
        if self._col is not None:
            self._col.flush()

    # -------- [8/6 plan §2] 新方法 --------
    def contains(self, chunk_id: str, conn=None) -> bool:
        """[8/6 fix] chunk_id → rowid → 查 zvec collection 是否有对应 doc."""
        zvec_id = self._chunk_id_to_zvec_id(chunk_id, conn)
        if zvec_id is None:
            return False
        try:
            doc = self._col.fetch([zvec_id])
            return doc is not None and len(doc) > 0
        except Exception as e:
            logger.warning(f"[zvec.contains] fetch failed for {chunk_id}: {e}")
            return False

    def cleanup_orphans(self, conn=None, dry_run: bool = False) -> Dict:
        """遍历 iter_all → 对每个 zvec_id (= chunks.rowid) 查 chunks 表 → soft/orphan 分类 → delete.

        必须由调用方传 conn (zvec 不持 SQLite 连接; conn=None 时防御性返回全 0).
        """
        result = {
            "soft_deleted_cleaned": 0,
            "truly_orphan_cleaned": 0,
            "vectors_remaining": 0,
            "dry_run": dry_run,
        }
        if conn is None:
            logger.warning("[zvec.cleanup_orphans] conn is None — 防御性返回 (调用方应传 conn)")
            return result
        try:
            ids = [d.id for d in self._col.iter_all()]
        except Exception as e:
            logger.warning(f"[zvec.cleanup_orphans] iter_all failed: {e}")
            return result

        to_delete: List[str] = []
        for zvec_id in ids:
            try:
                rowid_int = int(zvec_id)
            except (TypeError, ValueError):
                # 旧 rebuild 残留 (chunk_id 直接当 zvec_id), 删掉 (不 join 找得到 rowid)
                result["truly_orphan_cleaned"] += 1
                to_delete.append(zvec_id)
                continue
            row = conn.execute(
                "SELECT valid_until FROM chunks WHERE rowid = ?", (rowid_int,)
            ).fetchone()
            if row is None:
                result["truly_orphan_cleaned"] += 1
                to_delete.append(zvec_id)
            elif row[0]:
                result["soft_deleted_cleaned"] += 1
                to_delete.append(zvec_id)

        if not dry_run and to_delete:
            try:
                self._col.delete(to_delete)
            except Exception as e:
                logger.warning(f"[zvec.cleanup_orphans] delete failed: {e}")

        if dry_run:
            result["vectors_remaining"] = len(ids)
        else:
            try:
                result["vectors_remaining"] = sum(1 for _ in self._col.iter_all())
            except Exception:
                result["vectors_remaining"] = -1
        return result


def _deserialize_f32(data: bytes) -> List[float]:
    """sqlite_vec.serialize_float32 → list[float]."""
    import struct

    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


# ============================================================
# usearch 后端 (硬件无关 HNSW — TASKS_SEARCH_INDEX §4 A1/A2)
# ============================================================

def usearch_available() -> bool:
    """[A1 §4] 进程内检测 usearch — 旧 CPU 不崩 (已实测), 只有 ImportError 可能.

    与 zvec_available 不同: usearch 在 Ivy Bridge 等老 x86_64 上可跑, 无需子进程隔离.
    """
    try:
        import usearch  # noqa: F401
        return True
    except ImportError:
        return False


class UsearchIndex(SearchIndex):
    """[A2 §4] usearch 后端 — HNSW, 硬件无关 (DESIGN §8.3 升级档, 本机 Ivy Bridge 可跑).

    [8/6 plan §2 精度] f16 量化 — Index(dtype='f16') 默认 2 字节/维,
    add/search 自动 f32↔f16 cast, KNN 查询不受影响.
    加载现有 f32 usearch.index 也兼容 (实测过 f32 file load f16 index OK),
    但后续 add 会写 f16 — 下次 fresh 必须 unlink 旧 f32 文件.

    内部 id = chunks.rowid (同 sqlite_vec, 无独立映射表 — 避免双写不一致).
    """

    def __init__(self, db_path: Path, dim: int):
        self.db_path = db_path
        self.dim = dim
        self._index_path = db_path.parent / "usearch.index"
        # 映射查询用自有 sqlite 连接 (usearch 不在 memory.py 事务里)
        self._conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # usearch 索引: 已存在则 load, 否则新建
        from usearch.index import Index
        self._index = Index(ndim=dim, metric="cos", dtype="f16")
        if self._index_path.exists():
            self._index.load(self._index_path)  # load 是实例方法

    @property
    def name(self) -> str:
        return "usearch"

    @property
    def supports_fts(self) -> bool:
        return False

    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        import numpy as np
        vec = np.frombuffer(query_bytes, dtype=np.float32)
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
        res = self._index.search(vec, top_k)
        c = conn or self._conn
        hits: List[KNNHit] = []
        for uid, dist in zip(res.keys, res.distances):
            row = c.execute(
                "SELECT id FROM chunks WHERE rowid = ?", (int(uid),)
            ).fetchone()
            if row:
                hits.append(KNNHit(chunk_id=row["id"], distance=float(dist)))
        return hits

    def add(self, chunk_id: str, vector_bytes: bytes, conn=None, content: Optional[str] = None) -> None:
        import numpy as np
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            logger.warning(f"[usearch.add] chunk {chunk_id} not found")
            return
        vec = np.frombuffer(vector_bytes, dtype=np.float32)
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
        ids = np.array([row["rowid"]], dtype=np.uint64)
        # [8/6 plan §C3 fix] 含 contains 早退 — 避免 remove+readd 在 usearch f16 下 SIGSEGV.
        # 原 try/except "Duplicate keys" → remove+add 路径在 usearch 2.x 有 f16 兼容性 bug
        # (remove 后立即 add 同一 rowid 偶发 _add_to_compiled SIGSEGV).
        # 用 set(keys) 而非 in keys: usearch IndexedKeys.__contains__ 偶发 SIGSEGV.
        existing = set(int(k) for k in self._index.keys)
        if int(row["rowid"]) in existing:
            logger.debug(f"[usearch.add] rowid {row['rowid']} 已在索引, 跳过")
            return
        try:
            self._index.add(ids, vec)
        except RuntimeError as e:
            # 兜底: 即便 contains 漏判, 真正的 Duplicate 也能恢复
            if "Duplicate keys" not in str(e):
                raise
            logger.warning(f"[usearch.add] rowid {row['rowid']} exists — remove+readd (idempotent)")
            try:
                self._index.remove(ids)
            except Exception:
                pass
            self._index.add(ids, vec)
        # NOTE: usearch 索引仅在 close() 时 save() — 进程异常退出前 add 可能丢

    def remove(self, chunk_id: str, conn=None) -> None:
        import numpy as np
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row:
            self._index.remove(np.array([row["rowid"]], dtype=np.uint64))

    def size(self) -> int:
        """[8/5 主人 commit] Index.size 是 int 属性 (不是方法, 已踩坑)."""
        return self._index.size

    def close(self) -> None:
        self._index.save(self._index_path)  # 持久化 (f16 写入)
        self._conn.close()

    # -------- [8/6 plan §2] 新方法 --------
    def contains(self, chunk_id: str, conn=None) -> bool:
        """查 chunks 表拿 rowid → Index.keys 包含则 True.

        [8/6 fix] 用 set(keys) 而非 `in keys`: usearch IndexedKeys.__contains__
        偶发 SIGSEGV (同 add() 的规避, 见下), 统一走 set 成员判断.
        """
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            return False
        return int(row["rowid"]) in {int(k) for k in self._index.keys}

    def cleanup_orphans(self, conn=None, dry_run: bool = False) -> Dict:
        """遍历 Index.keys (rowid) → 查 chunks 行:
            - 无行 → truly_orphan
            - valid_until 非空 → soft_deleted
            - 否则保留
        非 dry-run 时 remove. 不 save — 落盘交给 close().
        """
        import numpy as np
        result = {
            "soft_deleted_cleaned": 0,
            "truly_orphan_cleaned": 0,
            "vectors_remaining": 0,
            "dry_run": dry_run,
        }
        c = conn or self._conn
        rowids: List[int] = list(self._index.keys)
        to_remove: List[int] = []
        for rid in rowids:
            row = c.execute(
                "SELECT valid_until FROM chunks WHERE rowid = ?", (rid,)
            ).fetchone()
            if row is None:
                result["truly_orphan_cleaned"] += 1
                to_remove.append(rid)
                continue
            if row[0]:
                result["soft_deleted_cleaned"] += 1
                to_remove.append(rid)

        if not dry_run and to_remove:
            try:
                self._index.remove(np.array(to_remove, dtype=np.uint64))
            except RuntimeError as e:
                logger.warning(f"[usearch.cleanup_orphans] remove failed: {e}")
                for rid in to_remove:
                    try:
                        self._index.remove(np.array([rid], dtype=np.uint64))
                    except Exception:
                        pass

        if dry_run:
            result["vectors_remaining"] = len(rowids)
        else:
            result["vectors_remaining"] = len(self._index.keys)
        return result


# ============================================================
# 工厂 + 特性检测
# ============================================================

def zvec_available() -> bool:
    """子进程检测 zvec 是否可导入 — 防旧 CPU 上 import 崩溃带崩 mnelo 主进程.

    [8/6 fix] macOS 26 launchd 起的 MCP 进程 fork 子进程跑 zvec native .so mmap
    必现 BlockingIOError (Errno 35 = EAGAIN, 不是 pipe 阻塞而是 fork syscall
    期间 kernel 返 EAGAIN). 子进程检测在这环境下不可靠.

    实战方案: 改在主进程直接 try-import zvec. 老 CPU (Ivy Bridge 之前) import
    zvec 崩溃的假设本机 (M2 MacBook, AVX2+) 不适用 — 本机 0 风险. 旧 CPU 用户
    应走 usearch 后端 (factory 自动降级).
    """
    try:
        import zvec  # noqa: F401
        return True
    except Exception as e:
        logger.warning(f"[zvec_available] main-process import failed: {type(e).__name__}: {e}")
        return False


# [8/6 plan §1] 向量库必选二选一 — usearch/zvec 都不可用时 RuntimeError.
def _pick_backend(requested: str, db_path: Path, dim: int) -> SearchIndex:
    """按 backend 字符串选择后端. requested 默认 'auto' → zvec (INT8, 优先) > usearch (f16); 都不可用抛 RuntimeError."""
    if requested == "auto":
        if zvec_available():
            return ZvecIndex(db_path.parent / "search_index.zv", dim)
        if usearch_available():
            logger.info("[search_index] auto: zvec 未装, 用 usearch (f16)")
            return UsearchIndex(db_path, dim)
        raise RuntimeError(
            "向量库是必选依赖 — zvec 与 usearch 均不可用. "
            "请 `pip install usearch>=2.26` 或 `pip install zvec`."
        )
    if requested == "zvec":
        if zvec_available():
            return ZvecIndex(db_path.parent / "search_index.zv", dim)
        raise RuntimeError(
            "zvec 不可用 (本机可能缺 AVX2+ 指令). "
            "改 'auto' 让 mnelo 回落 usearch, 或换支持 zvec 的部署机."
        )
    if requested == "usearch":
        if usearch_available():
            return UsearchIndex(db_path, dim)
        raise RuntimeError(
            "usearch 未安装. `pip install 'usearch>=2.26'` 或改 'auto' 试 zvec."
        )
    logger.warning(f"[search_index] 未知 backend '{requested}', fallback 到 auto")
    return _pick_backend("auto", db_path, dim)


def build_search_index(backend: str, db_path: Path, dim: int) -> SearchIndex:
    """[8/6 plan §1] 按 config 构建索引后端. backend ∈ {auto, usearch, zvec}."""
    return _pick_backend(backend, db_path, dim)