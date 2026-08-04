#!/usr/bin/env python3
"""
search_index.py — L1 检索层索引抽象 (DESIGN §3.6 / §8.3)

只抽象"向量索引"的 KNN 与写入; 召回业务逻辑 (asof / 过滤器 / RRF / lane 组合)
留在 memory.py。默认后端 sqlite-vec (vec0); 可选后端 zvec。

⚠️ zvec 后端说明 (重要):
  - zvec 0.6 原生扩展要求较新 CPU 指令 (AVX2+)。在旧 CPU 上 `import zvec` 直接
    Illegal instruction 崩溃 (进程级, 非异常)。因此 zvec 可用性检测必须在
    **子进程**中进行, 不可在 mnelo 进程内 try-import (会把 mnelo 一起带崩)。
  - zvec 后端代码按 zvec 0.6 类型化 API (zvec.pyi + model/*.py) 编写,
    **尚未在目标机 (Mac ARM64) 实测** — 需在部署机上验证后启用。
  - 后端不可用时自动回落 sqlite-vec, 不影响默认路径。
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
    """向量召回命中 — chunk_id 是唯一标识 (与后端解耦).

    sqlite-vec 后端: rowid → chunks.id 翻译在 knn() 内完成
    zvec 后端:       doc id = chunk_id, 直接返回
    """
    chunk_id: str
    distance: float


# ============================================================
# SearchIndex 抽象
# ============================================================

class SearchIndex(ABC):
    """向量索引抽象。写入 (add/remove) 与 KNN 查询是唯一契约。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名: 'sqlite_vec' | 'zvec'."""

    @abstractmethod
    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        """KNN 检索: query_bytes (序列化 float32 向量) → 按距离升序的命中.

        只返回 chunk_id + distance; valid_until/asof/filters 过滤由 memory.py
        在 chunk 侧做 (保证与 lane 业务逻辑解耦).
        conn: sqlite 后端用 (lane 独立连接); zvec 忽略.
        """

    @abstractmethod
    def add(self, chunk_id: str, vector_bytes: bytes, conn=None) -> None:
        """索引一条 chunk 的向量. chunk_id 需先存在于 chunks 表. 幂等.

        conn: sqlite 后端**必须**传调用方连接 (保证与写事务同连接, 能看到
        未提交的 chunk); 传了则**不 commit** (调用方管事务); 不传用自有连接.
        zvec 忽略 conn.
        """

    @abstractmethod
    def remove(self, chunk_id: str, conn=None) -> None:
        """删除一条 chunk 的向量索引. 幂等 (不存在也 OK). conn 语义同 add."""

    @abstractmethod
    def close(self) -> None:
        """释放资源 (连接/collection)."""


# ============================================================
# sqlite-vec 后端 (默认)
# ============================================================

class SQLiteVecIndex(SearchIndex):
    """vec0 后端 — 复用现有逻辑, 行为与 v0.5.x 完全一致 (回归安全)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA cache_size = -64000")
        self._conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.row_factory = sqlite3.Row

    @property
    def name(self) -> str:
        return "sqlite_vec"

    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        """vec0 MATCH + k= (修复后语法). 返回 rowid → chunk_id 翻译后的命中."""
        c = conn or self._conn
        old_factory = c.row_factory
        c.row_factory = sqlite3.Row
        try:
            rows = c.execute(
                """
                SELECT v.rowid AS v_rowid, v.distance AS distance
                FROM vectors v
                WHERE v.embedding MATCH ? AND k = ?
            """,
                (query_bytes, top_k),
            ).fetchall()
        except Exception as e:
            logger.warning(f"[sqlite_vec.knn] failed: {e}")
            return []
        finally:
            c.row_factory = old_factory

        hits: List[KNNHit] = []
        for r in rows:
            v_rowid = r["v_rowid"] if isinstance(r, sqlite3.Row) else r[0]
            distance = r["distance"] if isinstance(r, sqlite3.Row) else r[1]
            chunk = c.execute("SELECT id FROM chunks WHERE rowid = ?", (v_rowid,)).fetchone()
            if chunk:
                hits.append(KNNHit(chunk_id=chunk["id"], distance=float(distance)))
        return hits

    def add(self, chunk_id: str, vector_bytes: bytes, conn=None) -> None:
        """INSERT vector, rowid = chunks.rowid (1:1 映射), 冲突 REPLACE.

        conn 传入时: 用调用方连接 (同事务, 能看到未提交 chunk), 不 commit.
        conn 缺省: 用自有连接 + 自身 commit (独立场景).
        """
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            logger.warning(f"[sqlite_vec.add] chunk {chunk_id} not found, skip index")
            return
        chunk_rowid = row[0]
        try:
            c.execute("INSERT INTO vectors (rowid, embedding) VALUES (?, ?)", (chunk_rowid, vector_bytes))
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
            if "UNIQUE constraint" not in str(e) and "primary key" not in str(e):
                raise
            logger.warning(f"[sqlite_vec.add] rowid {chunk_rowid} exists — replacing")
            c.execute("DELETE FROM vectors WHERE rowid = ?", (chunk_rowid,))
            c.execute("INSERT INTO vectors (rowid, embedding) VALUES (?, ?)", (chunk_rowid, vector_bytes))
        if conn is None:
            self._conn.commit()

    def remove(self, chunk_id: str, conn=None) -> None:
        c = conn or self._conn
        row = c.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row:
            try:
                c.execute("DELETE FROM vectors WHERE rowid = ?", (row[0],))
                if conn is None:
                    self._conn.commit()
            except sqlite3.OperationalError as e:
                logger.warning(f"[sqlite_vec.remove] failed for {chunk_id}: {e}")

    def close(self) -> None:
        self._conn.close()


# ============================================================
# zvec 后端 (可选, 需目标机验证)
# ============================================================

class ZvecIndex(SearchIndex):
    """zvec 后端 — 进程内嵌向量库 (DESIGN §8.3 升级档).

    ⚠️ 未在本环境实测 (CPU 不支持 zvec 原生指令)。按 zvec 0.6 API 编写,
    需在部署机 (Mac ARM64 / 新 x86) 上跑 search_index_smoke 验证后启用。
    本类的 add/remove/knn 与 SQLiteVecIndex 语义对齐, 便于 memory.py 无感切换。
    """

    def __init__(self, collection_path: Path, dim: int):
        self.collection_path = collection_path
        self.dim = dim
        self._col = None  # type: ignore
        # 延迟导入 — import zvec 在旧 CPU 上会崩, 由工厂子进程检测把关
        # 存为实例属性供 _create_schema 使用 (module 局部变量在方法间不可见)
        import zvec  # noqa: F401

        self._zvec = zvec
        if not collection_path.exists():
            self._col = zvec.create_and_open(str(collection_path), zvec.CollectionOption())
            self._create_schema()
        else:
            self._col = zvec.open(str(collection_path), zvec.CollectionOption())

    def _create_schema(self) -> None:
        """建 schema: embedding (512d 稠密) + content (FTS 文本列, jieba 分词)."""
        zv = self._zvec
        schema = zv.CollectionSchema()
        schema.add_dense_vector_field(name="embedding", dim=self.dim)
        schema.add_text_field(name="content")  # FTS 列 — meta 路可走 zvec 原生检索
        schema.add_text_field(name="memory_type")
        schema.add_text_field(name="source")
        self._col.create(schema)
        self._col.create_index(field_name="embedding", index_type="HNSW")

    @property
    def name(self) -> str:
        return "zvec"

    def knn(self, query_bytes: bytes, top_k: int, conn=None) -> List[KNNHit]:
        # query_bytes 是 float32 序列化; zvec 接受 python list[float]
        vec = _deserialize_f32(query_bytes)
        zv = self._zvec
        docs = self._col.query(
            zv.Query(field_name="embedding", vector=vec, param=zv.HnswQueryParam(ef=top_k * 2))
        )
        hits = []
        for d in docs[:top_k]:
            hits.append(KNNHit(chunk_id=d.id, distance=float(d.score)))
        return hits

    def add(self, chunk_id: str, vector_bytes: bytes, conn=None) -> None:
        zv = self._zvec
        self._col.upsert(
            zv.Doc(
                id=chunk_id,
                fields={"content": ""},  # content 由调用方在 fts 场景填充; 本类只管向量
                vectors={"embedding": _deserialize_f32(vector_bytes)},
            )
        )

    def remove(self, chunk_id: str, conn=None) -> None:
        self._col.delete([chunk_id])

    def close(self) -> None:
        if self._col is not None:
            self._col.flush()


def _deserialize_f32(data: bytes) -> List[float]:
    """sqlite_vec.serialize_float32 → list[float]."""
    import struct

    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


# ============================================================
# 工厂 + 特性检测
# ============================================================

def zvec_available() -> bool:
    """子进程检测 zvec 是否可导入 — 防旧 CPU 上 import 崩溃带崩 mnelo 主进程."""
    code = "import zvec; print('OK')"
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0 and b"OK" in r.stdout
    except Exception:
        return False


def build_search_index(backend: str, db_path: Path, dim: int) -> SearchIndex:
    """按 config 构建索引后端. backend 不可用/不支持 → 回落 sqlite_vec."""
    if backend == "zvec":
        if not zvec_available():
            logger.warning("[search_index] zvec 不可用 (CPU 或安装问题), 回落 sqlite_vec")
            return SQLiteVecIndex(db_path)
        collection_path = db_path.parent / "search_index.zv"
        return ZvecIndex(collection_path, dim)
    # 默认
    return SQLiteVecIndex(db_path)
