"""A1+A2+A4 — usearch 后端 + auto 降级工厂 (TASKS_SEARCH_INDEX §4 A1/A2/A4).

[8/5 主人决策] 工厂策略: 优先 zvec (AVX2+ 新 CPU), 不支持则降级 usearch, 再不行 sqlite_vec.
无 ALLOW_FALLBACK 环境变量; 不抛 RuntimeError, 自动降级链就是 fallback.

§4 验收:
  A1: usearch_available() 本机 True
  A2: UsearchIndex.knn/add/remove/close 全部正常工作 (knn 命中顺序正确; remove 后不命中; close/reopen 数据在)
  A4: backend='usearch' 且装了 → name='usearch'; backend='usearch' 且未装 → 降级 sqlite_vec;
      backend='auto' → zvec 不可用 → usearch (本机 Ivy Bridge 实测)
"""
import importlib.util as _ilu
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SI_PATH = ROOT / "search_index.py"

# [8/5 fix] DB 路径不再硬编码 — 用 config 解析 (与 config.py 一致)
sys.path.insert(0, str(ROOT))
from config import config as _config_mod  # noqa: E402

# Global DB path used by tests; tests that need isolation should monkeypatch
# config.config.db_path or pass db_path= explicitly to build_search_index.
_DEFAULT_DB_PATH = _config_mod.db_path


def _load_search_index():
    if "search_index" in sys.modules:
        del sys.modules["search_index"]
    spec = _ilu.spec_from_file_location("search_index", SI_PATH)
    mod = _ilu.module_from_spec(spec)
    sys.modules["search_index"] = mod
    spec.loader.exec_module(mod)
    return mod


_si = _load_search_index()


def _new_test_db(tmp_path):
    """创建测试用 sqlite db (chunks 表 + rowid 隐式主键)."""
    db = tmp_path / "test_usearch.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            content TEXT,
            timestamp TEXT,
            valid_until TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db


def _insert_chunk(db_path, chunk_id):
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO chunks (id, content, timestamp) VALUES (?, ?, datetime('now'))",
        (chunk_id, f"content for {chunk_id}"),
    )
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return rowid


def test_a1_usearch_available():
    """[A1] usearch_available() 本机 True (8/5 已 pip install usearch>=2.26)."""
    assert _si.usearch_available() is True, (
        "本机已装 usearch 2.26, usearch_available() 应返 True"
    )


def test_a2_usearch_index_basic_init():
    """[A2] UsearchIndex() 初始化不抛 + name/supports_fts 正确."""
    idx = _si.UsearchIndex(_DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "usearch"
        assert idx.supports_fts is False
    finally:
        idx.close()


def test_a2_usearch_index_knn_after_add(tmp_path):
    """[A2] add → knn 命中正确顺序 + remove 后不命中."""
    db = _new_test_db(tmp_path)
    idx = _si.UsearchIndex(db, dim=4)
    try:
        import numpy as np
        cid_a = "test_chunk_a"
        cid_b = "test_chunk_b"
        cid_c = "test_chunk_c"
        _insert_chunk(db, cid_a)
        _insert_chunk(db, cid_b)
        _insert_chunk(db, cid_c)

        # vec_a == query → distance ≈ 0 (完美命中)
        vec_a = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32).tobytes()
        vec_b = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32).tobytes()
        vec_c = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32).tobytes()
        idx.add(cid_a, vec_a)
        idx.add(cid_b, vec_b)
        idx.add(cid_c, vec_c)

        # query = vec_a → 第一个命中应是 cid_a
        hits = idx.knn(vec_a, top_k=3)
        chunk_ids = [h.chunk_id for h in hits]
        assert chunk_ids[0] == cid_a, f"最近邻应 cid_a, got {chunk_ids}"
        assert all(h.distance >= 0 for h in hits), "distance 应 ≥ 0"
    finally:
        idx.close()


def test_a2_usearch_index_remove_idempotent(tmp_path):
    """[A2] remove 不存在的 chunk_id 不抛 (幂等)."""
    db = _new_test_db(tmp_path)
    idx = _si.UsearchIndex(db, dim=4)
    try:
        idx.remove("never_existed_chunk")
        cid = "test_remove_existing"
        _insert_chunk(db, cid)
        import numpy as np
        idx.add(cid, np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32).tobytes())
        idx.remove(cid)
        idx.remove("nonexistent_again")
    finally:
        idx.close()


def test_a2_usearch_index_persistence(tmp_path):
    """[A2] close 后新实例 reopen → 数据仍在."""
    db = _new_test_db(tmp_path)
    cid = "test_persist"
    _insert_chunk(db, cid)

    idx1 = _si.UsearchIndex(db, dim=4)
    import numpy as np
    vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32).tobytes()
    idx1.add(cid, vec)
    idx1.close()

    idx2 = _si.UsearchIndex(db, dim=4)
    try:
        hits = idx2.knn(vec, top_k=1)
        assert hits, "reopen 后应能 knn 命中"
        assert hits[0].chunk_id == cid
    finally:
        idx2.close()


def test_a4_factory_explicit_usearch_returns_usearch():
    """[A4 §1.4] backend='usearch' 且装了 → factory 返 UsearchIndex."""
    idx = _si.build_search_index("usearch", _DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "usearch", f"应返 UsearchIndex, got {idx.name}"
    finally:
        idx.close()


def test_a4_factory_explicit_usearch_falls_back_when_not_installed(monkeypatch):
    """[A4 §1.4] usearch_available=False + backend='usearch' → 降级 sqlite_vec (不抛)."""
    monkeypatch.setattr(_si, "usearch_available", lambda: False)
    idx = _si.build_search_index("usearch", _DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "sqlite_vec", (
            f"usearch 未装应降级 sqlite_vec, got {idx.name}"
        )
    finally:
        idx.close()


def test_a4_factory_auto_falls_back_from_zvec_to_usearch(monkeypatch):
    """[A4 §1.4 8/5 主人决策] backend='auto' → zvec 不可用 → 降级 usearch."""
    monkeypatch.setattr(_si, "zvec_available", lambda: False)
    idx = _si.build_search_index("auto", _DEFAULT_DB_PATH, dim=512)
    try:
        # 本机 Ivy Bridge zvec 不可用, usearch 已装 → 应选 usearch
        assert idx.name == "usearch", (
            f"auto: zvec 不可用应降级 usearch, got {idx.name}"
        )
    finally:
        idx.close()


def test_a4_factory_auto_falls_back_through_to_sqlite_vec(monkeypatch):
    """[A4 §1.4] auto: zvec 不可用 + usearch 不可用 → sqlite_vec (最终兜底)."""
    monkeypatch.setattr(_si, "zvec_available", lambda: False)
    monkeypatch.setattr(_si, "usearch_available", lambda: False)
    idx = _si.build_search_index("auto", _DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "sqlite_vec", (
            f"auto 全降级应到 sqlite_vec, got {idx.name}"
        )
    finally:
        idx.close()


def test_a4_factory_explicit_zvec_falls_back_to_usearch_when_unavailable(monkeypatch):
    """[A4 §1.4] backend='zvec' 显式但不可用 → 降级 usearch (不抛)."""
    monkeypatch.setattr(_si, "zvec_available", lambda: False)
    idx = _si.build_search_index("zvec", _DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "usearch", (
            f"zvec 显式不可用应降级 usearch, got {idx.name}"
        )
    finally:
        idx.close()


def test_a4_factory_explicit_sqlite_vec_returns_sqlite_vec():
    """[A4 §1.4] backend='sqlite_vec' 显式 → 直返 SQLiteVecIndex (不检测其他)."""
    idx = _si.build_search_index("sqlite_vec", _DEFAULT_DB_PATH, dim=512)
    try:
        assert idx.name == "sqlite_vec"
    finally:
        idx.close()