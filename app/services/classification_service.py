from typing import Dict, List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from loguru import logger
from app.core.llm_factory import get_classifier_llm
from app.schemas.common import ClassificationResult
from app.services.taxonomy_service import (
    classify_by_rules,
    classify_issue_type,
    load_taxonomy,
    get_short_label,
)
from app.prompts import ISSUE_CLASSIFICATION_PROMPT
from app.services.vector_service import vector_kb


class ClassificationService:
    def __init__(self):
        self.llm = get_classifier_llm()
        self.parser = JsonOutputParser(pydantic_object=ClassificationResult)
        # 从 prompts 模块导入问题分类提示词
        self.prompt = ChatPromptTemplate.from_template(ISSUE_CLASSIFICATION_PROMPT)
        self.chain = self.prompt | self.llm | self.parser

    def _get_historical_categories(self, text: str) -> str:
        """
        使用 RAG 检索历史相似案例的分类信息
        返回格式化的历史分类参考字符串
        """
        try:
            categories = vector_kb.get_historical_categories(text, k=2)
            if not categories:
                return "无历史参考"
            
            lines = []
            for i, cat in enumerate(categories, 1):
                cat_str = f"{i}. "
                if cat.get("category_l1"):
                    cat_str += f"分类: {cat['category_l1']}"
                    if cat.get("category_l2"):
                        cat_str += f"/{cat['category_l2']}"
                if cat.get("severity"):
                    cat_str += f", 严重度: {cat['severity']}"
                if cat.get("issue_type"):
                    cat_str += f", 类型: {cat['issue_type']}"
                if cat.get("content_preview"):
                    cat_str += f" (相似内容: {cat['content_preview']}...)"
                lines.append(cat_str)
            
            return "\n".join(lines) if lines else "无历史参考"
        except Exception as e:
            logger.warning(f"历史分类检索失败: {e}")
            return "无历史参考"

    async def classify(self, text: str) -> Dict[str, object]:
        rule_result = classify_by_rules(text)
        issue_type = classify_issue_type(text)
        if rule_result.get("category_l1") != "OTHER":
            rule_result["issue_type"] = issue_type
            return rule_result

        taxonomy = load_taxonomy()
        
        # RAG: 检索历史相似案例的分类
        historical_categories = self._get_historical_categories(text)
        
        try:
            result = await self.chain.ainvoke(
                {
                    "taxonomy_version": taxonomy.get("version"),
                    "taxonomy_tree": _format_taxonomy_tree(taxonomy),
                    "labels": ", ".join(taxonomy.get("labels", [])),
                    "short_labels": _format_short_labels(taxonomy),
                    "text": text,
                    "historical_categories": historical_categories,  # 新增：历史分类参考
                    "format_instructions": self.parser.get_format_instructions(),
                }
            )
            result.update(
                {
                    "taxonomy_version": taxonomy.get("version"),
                    "classification_strategy": "llm_with_rag" if historical_categories != "无历史参考" else "llm",
                    "rule_id": None,
                    "issue_type": issue_type,
                }
            )
            result["category_short"] = _normalize_short_label(
                result.get("category_short"),
                taxonomy,
                result.get("category_l1"),
                result.get("category_l2"),
            )
            return result
        except Exception as e:
            logger.warning(f"LLM 分类失败: {e}")
            rule_result["issue_type"] = issue_type
            return rule_result


def _normalize_short_label(
    short_label: str | None, taxonomy: Dict[str, object], l1: str | None, l2: str | None
) -> str:
    if not short_label:
        return get_short_label(taxonomy, l1, l2)
    cleaned = str(short_label).strip()
    if len(cleaned) < 2:
        return get_short_label(taxonomy, l1, l2)
    if len(cleaned) > 6:
        return cleaned[:6]
    return cleaned


def _format_short_labels(taxonomy: Dict[str, object]) -> str:
    short_map = taxonomy.get("short_labels", {})
    if not isinstance(short_map, dict):
        return "无"
    lines = []
    l1_map = short_map.get("l1", {})
    l2_map = short_map.get("l2", {})
    if isinstance(l1_map, dict) and l1_map:
        lines.append("L1: " + ", ".join([f"{k}={v}" for k, v in l1_map.items()]))
    if isinstance(l2_map, dict) and l2_map:
        lines.append("L2: " + ", ".join([f"{k}={v}" for k, v in l2_map.items()]))
    return "\n".join(lines) if lines else "无"


def _format_taxonomy_tree(taxonomy: Dict[str, object]) -> str:
    lines = []
    categories = taxonomy.get("categories", {})
    for l1, l2_list in categories.items():
        joined = ", ".join(l2_list)
        lines.append(f"- {l1}: {joined}")
    return "\n".join(lines)
