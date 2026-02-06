from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from loguru import logger
from app.core.llm_factory import get_fast_llm
from app.schemas.common import SentinelResult
from app.services.classification_service import ClassificationService
from app.prompts import RISK_SENTINEL_PROMPT
from app.services.vector_service import vector_kb


class SentinelAgent:
    def __init__(self):
        self.llm = get_fast_llm()
        self.parser = JsonOutputParser(pydantic_object=SentinelResult)
        self.classifier = ClassificationService()
        # 从 prompts 模块导入风险监控哨兵提示词
        self.prompt = ChatPromptTemplate.from_template(RISK_SENTINEL_PROMPT)
        self.chain = self.prompt | self.llm | self.parser

    def _assess_historical_risk(self, text: str) -> dict:
        """
        使用 RAG 检索历史案例，评估历史风险
        返回: {"risk_boost": int, "has_similar_high_risk": bool, "reason": str}
        """
        try:
            historical = vector_kb.get_historical_severity(text, k=2)
            
            if not historical["has_similar"]:
                return {"risk_boost": 0, "has_similar_high_risk": False, "reason": "no_similar_cases"}
            
            risk_boost = 0
            reason = ""
            
            # 如果历史案例包含高风险关键词，增加风险分数
            if historical["has_high_risk"]:
                risk_boost += 20
                reason = "similar_to_historical_high_risk_case"
            
            # 如果历史案例严重程度高，增加风险分数
            if historical["max_severity_score"] >= 60:  # S2 及以上
                risk_boost += 15
                reason = reason or "similar_to_high_severity_case"
            
            return {
                "risk_boost": risk_boost,
                "has_similar_high_risk": historical["has_high_risk"],
                "reason": reason or "similar_case_found",
                "similar_categories": historical["similar_categories"],
            }
        except Exception as e:
            logger.warning(f"历史风险评估失败: {e}")
            return {"risk_boost": 0, "has_similar_high_risk": False, "reason": "assessment_failed"}

    async def check_message(self, text: str) -> dict:
        try:
            # 1. LLM 分析当前消息
            result = await self.chain.ainvoke(
                {"text": text, "format_instructions": self.parser.get_format_instructions()}
            )
            
            # 2. RAG 检索历史高风险案例
            historical_assessment = self._assess_historical_risk(text)
            
            # 3. 综合评估：取 LLM 分析和历史风险的较高值
            original_score = result.get("risk_score", 0)
            risk_boost = historical_assessment.get("risk_boost", 0)
            
            if risk_boost > 0:
                # 风险分数增加，但不超过 100
                new_score = min(100, original_score + risk_boost)
                result["risk_score"] = new_score
                result["historical_risk_boost"] = risk_boost
                result["historical_risk_reason"] = historical_assessment.get("reason", "")
                logger.debug(f"RAG 风险增强: {original_score} -> {new_score} (boost: {risk_boost})")
                
                # 如果历史有高风险案例且当前分数偏低，考虑触发告警
                if historical_assessment.get("has_similar_high_risk") and new_score >= 50:
                    result["is_alert"] = True
            
            # 4. 分类
            classification = await self.classifier.classify(text)
            result.update(classification)
            
            return result
        except Exception as e:
            logger.error(f"哨兵分析失败: {e}")
            fallback = await self.classifier.classify(text)
            return {
                "is_alert": False,
                "risk_score": 0,
                "reason": "analysis_failed",
                **fallback,
            }
