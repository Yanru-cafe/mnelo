"""
[8/6 plan §12] SearchIndex 适配器测试 (DESIGN §3.6/§8.3).

本环境 CPU 不支持 zvec 原生指令 (import 即崩), 故:
- UsearchIndex: 用真实后端测 (本机可用)
- ZvecIndex: 用 fake zvec 模块验证 API 调用正确性 (真 zvec 需在部署机实测)
- build_search_index 工厂: 后端选择 + 必选二选一 (sqlite_vec 已出局, plan §1)

fake zvec 只实现 ZvecIndex 用到的 API 面 (含 iter_all, DataType.VECTOR_INT8),
用于验证"代码按 zvec 0.6 API 写对"。
"""
import math
import sys
import unittest
from pathlib import Path

# --- fake zvec 模块: 在 import search_index 前注入 sys.modules ---


class _FakeCollection:
    def __init__(self, path):
        self.path = path
        self.docs = {}  # id -> (vector: list[float], fields: dict)
        self.created_indexes = []

    def create(self, schema):
        self.schema = schema

    def create_index(self, field_name, index_type):
        self.created_indexes.append((field_name, index_type))

    def upsert(self, doc):
        self.docs[doc.id] = (doc.vectors.get("embedding", []), doc.fields)
        return None

    def delete(self, ids):
        for i in ids:
            self.docs.pop(i, None)
        return []

    def iter_all(self):
        """[8/6 plan §12] ZvecIndex.cleanup_orphans/contains 调 iter_all."""
        for doc_id in list(self.docs.keys()):
            from types import SimpleNamespace
            vec, fields = self.docs[doc_id]
            yield SimpleNamespace(id=doc_id, vectors={"embedding": vec}, fields=fields)

    def query(self, q):
        # FakeZvec.Query 返回 dict; 兼容属性访问 (真实 zvec Query 是 dataclass)
        v = q["vector"] if isinstance(q, dict) else q.vector
        scored = []
        for doc_id, (vec, fields) in self.docs.items():
            if not vec or not v or len(vec) != len(v):
                continue
            dot = sum(a * b for a, b in zip(vec, v))
            scored.append((dot, doc_id))
        scored.sort(key=lambda x: -x[0])
        from types import SimpleNamespace

        return [SimpleNamespace(id=i, score=s, vectors={"embedding": []}, fields={}) for s, i in scored]

    def flush(self):
        return None


class _FakeZvec:
    def __init__(self):
        self.collections = {}
        self.last_collection = None

    def CollectionOption(self):
        return {}

    def HnswQueryParam(self, ef=100):
        return {"ef": ef}

    def Query(self, field_name=None, id=None, vector=None, param=None, fts=None):
        return {"field_name": field_name, "id": id, "vector": vector, "param": param, "fts": fts}

    def Doc(self, id=None, score=None, vectors=None, fields=None):
        from types import SimpleNamespace

        return SimpleNamespace(id=id, score=score, vectors=vectors or {}, fields=fields or {})

    class CollectionSchema:
        def __init__(self):
            self.fields = []

        def add_dense_vector_field(self, name, dim, data_type=None):
            # [8/6 plan §2] ZvecIndex 传 data_type=DataType.VECTOR_INT8
            self.fields.append(("dense", name, dim, data_type))

        def add_text_field(self, name):
            self.fields.append(("text", name))

    def create_and_open(self, path, option):
        col = _FakeCollection(path)
        self.collections[path] = col
        self.last_collection = col
        return col

    def open(self, path, option):
        col = self.collections.get(path) or _FakeCollection(path)
        self.collections[path] = col
        self.last_collection = col
        return col

    class DataType:
        """[8/6 plan §2] ZvecIndex 期望 DataType.VECTOR_INT8."""
        VECTOR_INT8 = "VECTOR_INT8"


def _install_fake_zvec():
    fz = _FakeZvec()
    sys.modules["zvec"] = fz
    return fz


# [8/6 plan §12] TestSQLiteVecIndex 整类删除 (sqlite_vec 已出局, plan §1)
class TestZvecBackendWithFake(unittest.TestCase):
    """fake zvec — 验证 ZvecIndex 按 zvec 0.6 API 调用正确 (含 INT8 精度)."""

    def setUp(self):
        self.fz = _install_fake_zvec()
        import search_index

        # 重载以确保用刚注入的 fake zvec
        search_index.zvec_available = lambda: True
        self.module = search_index

    def test_01_init_creates_schema(self):
        idx = self.module.ZvecIndex(Path("/tmp/fake_zv_test"), dim=512)
        self.assertEqual(idx.name, "zvec")
        col = self.fz.last_collection
        self.assertEqual(col.created_indexes, [("embedding", "HNSW")])
        idx.close()

    def test_02_add_knn_roundtrip(self):
        import struct

        idx = self.module.ZvecIndex(Path("/tmp/fake_zv_round"), dim=4)
        v1 = struct.pack("4f", *[1.0, 0.0, 0.0, 0.0])
        v2 = struct.pack("4f", *[0.0, 1.0, 0.0, 0.0])
        idx.add("chunk_a", v1)
        idx.add("chunk_b", v2)
        hits = idx.knn(struct.pack("4f", *[0.99, 0.01, 0.0, 0.0]), 2)
        self.assertEqual([h.chunk_id for h in hits], ["chunk_a", "chunk_b"])
        idx.remove("chunk_a")
        hits2 = idx.knn(struct.pack("4f", *[0.99, 0.01, 0.0, 0.0]), 2)
        self.assertNotIn("chunk_a", [h.chunk_id for h in hits2])
        idx.close()

    def test_03_size_and_contains_and_cleanup(self):
        """[8/6 plan §12] ZvecIndex.size/contains/cleanup_orphans API 面."""
        import struct

        idx = self.module.ZvecIndex(Path("/tmp/fake_zv_sc"), dim=4)
        v = struct.pack("4f", *[1.0, 0.0, 0.0, 0.0])
        idx.add("chunk_a", v)
        idx.add("chunk_b", v)
        # size
        self.assertEqual(idx.size(), 2)
        # contains
        self.assertTrue(idx.contains("chunk_a"))
        self.assertFalse(idx.contains("chunk_x"))
        # cleanup_orphans: conn=None 防御性返回
        r0 = idx.cleanup_orphans(conn=None, dry_run=True)
        self.assertEqual(r0["truly_orphan_cleaned"], 0)
        # cleanup_orphans: 配 fake conn (返回空 → 全 orphan)
        class FakeConn:
            def execute(self, sql, params=()):
                class _R:
                    def fetchone(self_inner):
                        return None  # chunk missing → orphan
                return _R()
        r = idx.cleanup_orphans(conn=FakeConn(), dry_run=False)
        self.assertEqual(r["truly_orphan_cleaned"], 2)
        self.assertEqual(idx.size(), 0)
        idx.close()

    def test_04_deserialize_f32(self):
        import struct

        from search_index import _deserialize_f32

        self.assertEqual(_deserialize_f32(struct.pack("2f", 1.5, -2.0)), [1.5, -2.0])


class TestFactory(unittest.TestCase):
    """[8/6 plan §12] build_search_index 后端选择 + 必选二选一 (sqlite_vec 已出局)."""

    def setUp(self):
        import search_index

        self.module = search_index
        from config import resolve_db_path

        self.db_path = resolve_db_path()

    def test_01_auto_returns_usearch_or_zvec(self):
        """[8/6 plan §1] auto 必须返回 usearch 或 zvec (sqlite_vec 已出局)."""
        idx = self.module.build_search_index("auto", self.db_path, 512)
        self.assertIn(idx.name, ("usearch", "zvec"),
                      f"auto 应二选一, got {idx.name}")
        idx.close()

    def test_02_usearch_explicit_returns_usearch(self):
        idx = self.module.build_search_index("usearch", self.db_path, 512)
        self.assertEqual(idx.name, "usearch")
        idx.close()

    def test_03_zvec_unavailable_raises(self):
        """[8/6 plan §1] 显式 zvec 不可用 → RuntimeError (不再回落 usearch)."""
        self.module.zvec_available = lambda: False
        with self.assertRaises(RuntimeError):
            self.module.build_search_index("zvec", self.db_path, 512)

    def test_04_usearch_unavailable_raises(self):
        """[8/6 plan §1] 显式 usearch 未安装 → RuntimeError (不再回落 sqlite_vec)."""
        self.module.usearch_available = lambda: False
        with self.assertRaises(RuntimeError):
            self.module.build_search_index("usearch", self.db_path, 512)

    def test_05_auto_both_unavailable_raises(self):
        """[8/6 plan §1] auto 双不可用 → RuntimeError."""
        self.module.zvec_available = lambda: False
        self.module.usearch_available = lambda: False
        with self.assertRaises(RuntimeError):
            self.module.build_search_index("auto", self.db_path, 512)

    def test_06_zvec_available_builds_zvec(self):
        _install_fake_zvec()
        self.module.zvec_available = lambda: True
        idx = self.module.build_search_index("zvec", self.db_path, 512)
        self.assertEqual(idx.name, "zvec")
        idx.close()

    def test_07_unknown_backend_falls_back_to_auto(self):
        """[8/6 plan §1] 未知 backend → warning + fallback auto."""
        self.module.zvec_available = lambda: False
        self.module.usearch_available = lambda: True
        idx = self.module.build_search_index("weird_backend", self.db_path, 512)
        self.assertEqual(idx.name, "usearch")
        idx.close()
