import os
import re
from functools import lru_cache
from typing import Dict, Optional
import yaml


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, object]:
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "taxonomy.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_taxonomy(data: Dict[str, object]) -> None:
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "taxonomy.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    load_taxonomy.cache_clear()


def classify_by_rules(text: str) -> Dict[str, Optional[object]]:
    taxonomy = load_taxonomy()
    lowered = text.lower()
    for rule in taxonomy.get("rules", []):
        for kw in rule.get("keywords", []):
            if kw in text or kw.lower() in lowered:
                category_short = _short_label(rule.get("l1"), rule.get("l2"), taxonomy)
                return {
                    "category_l1": rule.get("l1"),
                    "category_l2": rule.get("l2"),
                    "category_short": category_short,
                    "labels": rule.get("labels", []),
                    "severity": _match_severity(text, taxonomy),
                    "is_bug": True,
                    "confidence": 0.9,
                    "taxonomy_version": taxonomy.get("version"),
                    "classification_strategy": "rule",
                    "rule_id": rule.get("id"),
                }

    default_cfg = taxonomy.get("default", {})
    category_short = _short_label(
        default_cfg.get("l1", "OTHER"), default_cfg.get("l2", "OTHER"), taxonomy
    )
    return {
        "category_l1": default_cfg.get("l1", "OTHER"),
        "category_l2": default_cfg.get("l2", "OTHER"),
        "category_short": category_short,
        "labels": [],
        "severity": _match_severity(text, taxonomy),
        "is_bug": False,
        "confidence": 0.4,
        "taxonomy_version": taxonomy.get("version"),
        "classification_strategy": "rule",
        "rule_id": None,
    }


def classify_issue_type(text: str) -> str:
    """
    四维度精准分类（规则优先）
    优先级：产品缺陷 > 问题反馈 > 客户需求 > 使用咨询
    """
    taxonomy = load_taxonomy()
    lowered = text.lower()
    
    # 按优先级排序（priority 越小越优先）
    issue_types = sorted(
        taxonomy.get("issue_types", []),
        key=lambda x: x.get("priority", 99)
    )
    
    for rule in issue_types:
        keywords = rule.get("keywords", [])
        for kw in keywords:
            if kw in text or kw.lower() in lowered:
                return rule.get("type", taxonomy.get("default_issue_type", "问题反馈"))
    
    return taxonomy.get("default_issue_type", "问题反馈")


def analyze_emotion_keywords(text: str) -> dict:
    """
    基于关键词的情绪初判（供LLM参考）
    """
    taxonomy = load_taxonomy()
    emotion_kw = taxonomy.get("emotion_keywords", {})
    churn_kw = taxonomy.get("churn_risk_keywords", [])
    
    lowered = text.lower()
    
    # 检查负面高风险词
    for kw in emotion_kw.get("negative_high", []):
        if kw in text or kw in lowered:
            return {"emotion_hint": "负面", "risk_hint": "high", "matched": kw}
    
    # 检查流失风险词
    for kw in churn_kw:
        if kw in text or kw in lowered:
            return {"emotion_hint": "负面", "risk_hint": "churn", "matched": kw}
    
    # 检查中等负面词
    for kw in emotion_kw.get("negative_medium", []):
        if kw in text or kw in lowered:
            return {"emotion_hint": "负面", "risk_hint": "medium", "matched": kw}
    
    # 检查正面词
    for kw in emotion_kw.get("positive", []):
        if kw in text or kw in lowered:
            return {"emotion_hint": "正面", "risk_hint": "low", "matched": kw}
    
    return {"emotion_hint": "中性", "risk_hint": "normal", "matched": None}


def _match_severity(text: str, taxonomy: Dict[str, object]) -> str:
    for rule in taxonomy.get("severity_rules", []):
        if re.search(rule.get("pattern", ""), text):
            return rule.get("severity", "S1")
    return "S1"


def _short_label(l1: Optional[str], l2: Optional[str], taxonomy: Dict[str, object]) -> str:
    short_map = taxonomy.get("short_labels", {})
    l1_map = short_map.get("l1", {}) if isinstance(short_map, dict) else {}
    l2_map = short_map.get("l2", {}) if isinstance(short_map, dict) else {}
    label = None
    if l2 and l2 in l2_map:
        label = l2_map.get(l2)
    elif l1 and l1 in l1_map:
        label = l1_map.get(l1)
    if not label:
        for item in [l2, l1]:
            if item and item != "OTHER":
                label = str(item)
                break
    label = (label or "其他").strip()
    if len(label) < 2:
        return "其他"
    return label[:6]


def get_short_label(taxonomy: Dict[str, object], l1: Optional[str], l2: Optional[str]) -> str:
    return _short_label(l1, l2, taxonomy)
