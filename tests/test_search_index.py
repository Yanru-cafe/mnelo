"""
[zvec 集成] SearchIndex 适配器测试 (DESIGN §3.6/§8.3).

本环境 CPU 不支持 zvec 原生指令 (import 即崩), 故:
- SQLiteVecIndex: 用真实后端测 (回归安全网)
- ZvecIndex: 用 fake zvec 模块验证 API 调用正确性 (真 zvec 需在部署机实测)
- build_search_index 工厂: 后端选择 + zvec 不可用时回落

fake zvec 只实现 ZvecIndex 用到的 API 面, 用于验证"代码按 zvec 0.6 API 写对"。
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

        def add_dense_vector_field(self, name, dim):
            self.fields.append(("dense", name, dim))

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


def _install_fake_zvec():
    fz = _FakeZvec()
    sys.modules["zvec"] = fz
    return fz


class TestSQLiteVecIndex(unittest.TestCase):
    """真实 sqlite_vec 后端 — 回归安全网."""

    @classmethod
    def setUpClass(cls):
        from search_index import SQLiteVecIndex
        from config import resolve_db_path

        cls.db_path = resolve_db_path()
        cls.index = SQLiteVecIndex(cls.db_path)
        from memory import Memory

        cls.mem = Memory()

    @classmethod
    def tearDownClass(cls):
        cls.mem.close()
        cls.index.close()

    def test_01_add_knn_remove(self):
        import struct

        from embedder import embed_bytes

        # 写入两条 + 建 chunks 记录 (让 knn 能翻译 rowid→chunk_id)
        cid1 = self.mem.remember("search_index test 一只蓝色狐狸", source="test_search_index")
        cid2 = self.mem.remember("search_index test 一只红色狐狸", source="test_search_index")
        v = embed_bytes("一只蓝色狐狸")
        hits = self.index.knn(v, 5)
        self.assertTrue(any(h.chunk_id == cid1 for h in hits), "knn 应命中 cid1")
        self.index.remove(cid1)
        hits2 = self.index.knn(v, 5)
        self.assertFalse(any(h.chunk_id == cid1 for h in hits2), "remove 后不应再命中")
        self.index.remove(cid2)


class TestZvecBackendWithFake(unittest.TestCase):
    """fake zvec — 验证 ZvecIndex 按 zvec 0.6 API 调用正确."""

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

    def test_03_deserialize_f32(self):
        import struct

        from search_index import _deserialize_f32

        self.assertEqual(_deserialize_f32(struct.pack("2f", 1.5, -2.0)), [1.5, -2.0])


class TestFactory(unittest.TestCase):
    """build_search_index 后端选择 + 回落."""

    def setUp(self):
        import search_index

        self.module = search_index
        from config import resolve_db_path

        self.db_path = resolve_db_path()

    def test_01_default_is_sqlite_vec(self):
        idx = self.module.build_search_index("sqlite_vec", self.db_path, 512)
        self.assertEqual(idx.name, "sqlite_vec")
        idx.close()

    def test_02_zvec_unavailable_falls_back(self):
        self.module.zvec_available = lambda: False
        idx = self.module.build_search_index("zvec", self.db_path, 512)
        self.assertEqual(idx.name, "sqlite_vec", "zvec 不可用应回落 sqlite_vec")
        idx.close()

    def test_03_zvec_available_builds_zvec(self):
        _install_fake_zvec()
        self.module.zvec_available = lambda: True
        idx = self.module.build_search_index("zvec", self.db_path, 512)
        self.assertEqual(idx.name, "zvec")
        idx.close()
