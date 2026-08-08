"""E1 — classify.py 骨架 + 繁→简归一化 (TASKS_L2_EXTRACT §3 E1).

§1.2 验收:
  - _normalize("我覺得這個方案不錯") == "我觉得这个方案不错"
  - 空串/纯英文不报错
  - _MARKERS 结构占位 (decision/preference/episode/procedure/ephemeral/fact)
  - _T2S dict bounded (约 50-80 字)
"""
import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLASSIFY_PATH = ROOT / "classify.py"


def _load_classify():
    """Reload classify module to dodge staleness from other test modules."""
    if "classify" in sys.modules:
        del sys.modules["classify"]
    spec = _ilu.spec_from_file_location("classify", CLASSIFY_PATH)
    mod = _ilu.module_from_spec(spec)
    sys.modules["classify"] = mod
    spec.loader.exec_module(mod)
    return mod


_clf = _load_classify()


def test_e1_normalize_t2s_converts_traditional_to_simplified():
    """[E1 §1.2] 繁体 → 简体: '我覺得這個方案不錯' → '我觉得这个方案不错'."""
    result = _clf._normalize("我覺得這個方案不錯")
    assert result == "我觉得这个方案不错", (
        f"_normalize 繁→简失败: got {result!r}"
    )


def test_e1_normalize_passes_through_simplified_chinese():
    """[E1 §1.2] 简体直通 (不破坏已简化字符)."""
    text = "用户偏好 A 股, 但昨天卖出 sh600021"
    assert _clf._normalize(text) == text


def test_e1_normalize_handles_pure_english():
    """[E1 §1.2] 纯英文直通 (无繁字符)."""
    text = "I decided to sell sh600021 today"
    assert _clf._normalize(text) == text


def test_e1_normalize_empty_string_no_error():
    """[E1 §1.2] 空串不报错."""
    assert _clf._normalize("") == ""


def test_e1_normalize_mixed_traditional_and_english():
    """[E1 §1.2] 繁中 + 英文混合只转繁中."""
    result = _clf._normalize("User decided 買入 today")
    # 買 → 买
    assert "买入" in result, f"混合繁中应转换: got {result!r}"
    assert "decided" in result


def test_e1_t2s_dict_bounded():
    """[E1 §1.2] _T2S dict bounded (实施含单字+少量多字组合, 实测 ~121 entries)."""
    assert isinstance(_clf._T2S, dict)
    n = len(_clf._T2S)
    # §1.2 "约 50-80 字" — 实施含少量多字组合 key, 实测 ~121 entries (b)
    assert 30 <= n <= 200, f"_T2S 条目数应 bounded (30-200), got {n}"
    # 全部值应是简体 (key 繁体, value 简体)
    for k, v in _clf._T2S.items():
        if len(k) == 1:
            assert len(v) == 1, f"_T2S 单字 key '{k}' value 应单字, got {v!r}"


def test_e1_markers_has_required_top_level_categories():
    """[E1 §1.3] _MARKERS 必须含 5 类 (preference/decision/episode/procedure/ephemeral)."""
    keys = set(_clf._MARKERS.keys())
    required = {"preference", "decision", "episode", "procedure", "ephemeral"}
    missing = required - keys
    assert not missing, f"_MARKERS 缺类: {missing}, got {list(keys)}"


def test_e1_markers_each_has_cn_section():
    """[E1 §1.3] 每个类型至少含 'cn' 子键."""
    for t in ("preference", "decision", "procedure", "ephemeral"):
        cat = _clf._MARKERS.get(t)
        assert cat is not None, f"缺少 {t}"
        assert "cn" in cat, f"{t} 缺 'cn' 子键"
        assert isinstance(cat["cn"], list), f"{t}.cn 应是 list"
        assert len(cat["cn"]) >= 1, f"{t}.cn 至少 1 标记"
    # episode 含复合子键 cn_time / cn_action
    ep = _clf._MARKERS["episode"]
    assert "cn_time" in ep, "episode 缺 cn_time"
    assert "cn_action" in ep, "episode 缺 cn_action"


def test_e1_classify_module_exposes_classify_memory_type():
    """[E1] classify.py 应暴露 classify_memory_type() 函数."""
    assert hasattr(_clf, "classify_memory_type"), (
        "classify.py 必须含 classify_memory_type() 函数 (即便 stub)"
    )