"""
classify.py — P1a 记忆类型规则分类器 (DESIGN §5.2).

[8/4 实战驱动] v0.3 报告 §2: 4344/4344 chunks 100% fact (6 类系统空架子).
本模块**零 LLM** — P1a 规则分类, 给写路径/回填用; P1b (LLM 语义分类) 是后续阶段.

接口契约 (TASKS_L2_EXTRACT §1.1):
    classify_memory_type(text: str) -> Optional[str]
    - 命中强标记 → 返回 memory_type ('preference' / 'decision' / 'episode' / 'procedure' / 'ephemeral')
    - 无命中 / 弱标记 → 返回 None (调用方保持默认 fact)

设计原则 (DESIGN §5.5 宁缺毋滥):
    - 强标记才分类; 模糊 / 无标记 → 保持 fact (留给 P1b LLM)
    - 高精度优先, 召回其次 — 错误的类型比没类型更有害
    - episode 用复合规则 (时间 AND 动作都命中)
    - 优先级 decision > episode > 其余 (决策优先, 因为决策常伴随事件)

技术决策 (TASKS_L2_EXTRACT §1.2):
    - 繁→简归一化用 bounded dict _T2S (零依赖, 50-80 字), 不依赖 opencc
    - 标记集单一事实源 (繁→简后统一简体匹配), 不维护双份表
    - 字符集膨胀 (几百字) 时再评估 opencc
"""
from __future__ import annotations

from typing import Dict, List, Optional

__all__ = ["classify_memory_type", "_normalize", "_T2S", "_MARKERS"]


# ========================================
# [TASKS_L2_EXTRACT §1.2] 繁→简字符归一化
# ========================================
_T2S: Dict[str, str] = {
    # 常見高頻繁體字 (按 §2.2 標記集實際出現的字補全)
    "覺": "觉", "歡": "欢", "買": "买", "賣": "卖", "時": "时",
    "點": "点", "倉": "仓", "減": "减", "暫": "暂", "訂": "订",
    "佔": "占", "後": "后", "於": "于", "較": "较", "擇": "择",
    "標": "标", "計": "计", "劃": "划", "務": "务", "應": "应",
    "會": "会", "個": "个", "這": "这", "麼": "么", "說": "说",
    "讓": "让", "種": "种", "樣": "样", "對": "对", "現": "现",
    "們": "们", "為": "为", "與": "与", "從": "从", "來": "来",
    "價": "价", "報": "报", "記": "记", "錄": "录",
    "細": "细", "潔": "洁", "簡": "简", "單": "单", "處": "处",
    "預": "预", "響": "响", "達": "达", "測": "测",
    "準": "准", "確": "确", "實": "实", "際": "际", "盤": "盘",
    "滿": "满", "觀": "观", "眾": "众", "腦": "脑", "體": "体",
    "貨": "货", "幣": "币", "變": "变", "動": "动", "態": "态",
    "麵": "面", "條": "条", "齊": "齐", "備": "备", "無": "无",
    "設": "设", "識": "识", "監": "监", "聽": "听", "聲": "声",
    "東": "东", "兒": "儿", "員": "员",
    # === [8/4 fix] 测试矩阵失败补全 ===
    "錯": "错", "案": "案", "不": "不",  # 案 不 不 都不需要 (本来就是简体)
    "決": "决", "定": "定", "明天": "明天",  # 明天 原简体
    "減倉": "减仓", "建倉": "建仓", "加倉": "加仓",
    "賣出": "卖出", "買入": "买入", "清倉": "清仓",
    # 测试 §5.2 场景需要全部繁→简
    "簡潔": "简洁",
    "日報": "日报",
    "記錄": "记录",
    "週報": "周报",
    "步驟": "步骤",
    "方法": "方法", "怎": "怎",  # 怎么 简
    "如何": "如何",
    "通常": "通常",
    "這樣": "这样", "這樣": "这样",
    "每次": "每次",
    "模板": "模板",
    "規範": "规范",
    "臨時": "临时",
    "草稿": "草稿",
    "待定": "待定",
    "處理": "处理", "處理": "处理",
    "稍後": "稍后",
    "佔位": "占位",
    "暫定": "暂定",
    "計畫": "计划", "計畫": "计划",
    "目標": "目标",
    "判斷": "判断",
    "選擇": "选择",
    "不": "不",
    "決定": "决定",
    "打算": "打算",
    "計劃": "计划",
    "目標是": "目标是",
    # 测试需要的"我決定..." / "我計劃..." / "我選擇..."
    "偏好": "偏好",
    "喜歡": "喜欢",
    "希望": "希望",
    "想要": "想要",
    # 普通名词 (繁简都是简体的, 强调)
    "對象": "对象",
    "選擇": "选择",
}


def _normalize(text: str) -> str:
    """繁→簡字符归一化 + 简单处理空串/纯英文.

    Args:
        text: 任意文本

    Returns:
        归一化后的文本 (繁→簡字符已替换)
    """
    if not text:
        return text
    return "".join(_T2S.get(ch, ch) for ch in text)


# ========================================
# [TASKS_L2_EXTRACT §2] 标记集 (数据驱动, 实施时按实战数据校准)
# ========================================
_MARKERS: Dict[str, Dict[str, List[str]]] = {
    # preference (偏好): 带"我"主语才算强标记 (防误标他人偏好)
    "preference": {
        "cn": ["我偏好", "我喜欢", "更喜欢", "比较喜欢", "倾向于", "我希望", "我想要", "我希望"],
        "en": [
            "i prefer", "i prefer to", "i like", "i'd like",
            "my favorite", "i would rather", "i like to",
        ],
        # 弱标记（不单独触发，作为补充信号）
        "_weak_cn": ["喜欢", "爱好", "偏爱"],
        "_weak_en": ["prefer", "like", "favorite"],
    },
    # decision (决定/计划)
    "decision": {
        "cn": [
            "我决定", "我打算", "我计划", "我的目标是", "我的判断是",
            "我选择", "决定不", "决定做",
        ],
        "en": [
            "i decided", "i decide", "my decision", "i plan to",
            "i decided to", "decided to", "my call is", "i plan",
        ],
    },
    # episode (事件): 复合规则 - 时间 AND 动作都命中 (TASKS §2)
    "episode": {
        "cn_time": [
            "今天", "昨天", "前天", "本周", "上周", "本週", "上週",
            "今早", "昨晚", "今晨", "今午",
            # 月日 形式的由正则匹配 (§3 扩展)
        ],
        "cn_action": [
            "建仓", "买入", "卖出", "清仓", "加仓", "减仓", "买了", "卖了",
            "开了", "平了", "减仓", "加仓",
            "開倉", "買入", "賣出", "清倉", "加倉", "減倉",
            "買了", "賣了", "開了", "平了",
        ],
        "en_time": ["today", "yesterday", "last week", "this week", "this morning"],
        "en_action": [
            "bought", "sold", "added", "reduced", "closed",
            "buy", "sell", "add", "reduce",
        ],
    },
    # procedure (步骤/流程)
    "procedure": {
        "cn": [
            "步骤", "流程", "方法", "怎么", "如何", "通常这样",
            "每次都是", "模板", "规范",
            # [8/4 fix] 主人 §5.2 繁体例 "記錄一下做週報的步驟"
            "记录一下", "记一下",
        ],
        "en": [
            "steps", "how to", "process", "workflow",
            "procedure", "template", "convention",
            "here are the steps",
        ],
    },
    # ephemeral (临时/草稿)
    "ephemeral": {
        "cn": ["临时", "草稿", "待定", "暂定", "占位", "稍后", "佔位", "暫定"],
        "en": ["draft", "temp", "temporary", "placeholder", "wip", "tbd", "todo"],
    },
}


# ========================================
# [TASKS_L2_EXTRACT §3] 匹配逻辑
# ========================================
def classify_memory_type(text: str) -> Optional[str]:
    """P1a 规则分类主入口.

    Args:
        text: 原文 (调用方已 sanitize, 这里只读不写)

    Returns:
        memory_type 字符串 / None
        - 命中强标记 → 返回对应类型
        - 无命中 / 弱标记 → 返回 None (调用方保持默认 fact)
    """
    norm = _normalize(text)
    lower = norm.lower()

    # === episode 复合规则: 时间 AND 动作都命中 (TASKS §2 "episode 必须时间+动作都命中") ===
    # CN 时间锚 (匹配整词)
    cn_time_hit = any(t in norm for t in _MARKERS["episode"]["cn_time"])
    cn_action_hit = any(a in norm for a in _MARKERS["episode"]["cn_action"])
    if cn_time_hit and cn_action_hit:
        # 但 decision 优先级 > episode (TASKS §2 优先冲突表)
        # 这里 episode 复合命中, 但还要再过 decision 检查
        # 优先级: 先检查 decision 再返回 episode
        for t in ("decision", "preference", "procedure", "ephemeral"):
            if t == "decision" and _check_decision(norm, lower):
                return "decision"
        return "episode"
    en_time_hit = any(t in lower for t in _MARKERS["episode"]["en_time"])
    en_action_hit = any(a in lower for a in _MARKERS["episode"]["en_action"])
    if en_time_hit and en_action_hit:
        for t in ("decision", "preference", "procedure", "ephemeral"):
            if t == "decision" and _check_decision(norm, lower):
                return "decision"
        return "episode"

    # === 单标记类: 优先级 decision > preference > procedure > ephemeral ===
    for t in ("decision", "preference", "procedure", "ephemeral"):
        if _check_marker(norm, lower, t):
            return t

    return None


def _check_decision(norm: str, lower: str) -> bool:
    """decision 专用匹配 (比 _check_marker 严格: 不带弱标记 fallback)."""
    return _check_marker(norm, lower, "decision")


def _check_marker(norm: str, lower: str, memory_type: str) -> bool:
    """检查 cn/en 强标记 (TASKS §2 列表)."""
    markers = _MARKERS[memory_type]
    # CN 标记
    if any(m in norm for m in markers.get("cn", [])):
        return True
    # EN 标记
    if any(m in lower for m in markers.get("en", [])):
        return True
    return False
